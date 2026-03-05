#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
02_LAJ_RAG_loop.py (vLLM RAG / C5 factual accuracy)
------------------------------------------------------------
LLM-as-a-Judge evaluation using Retrieved Context (RAG) via vLLM.

Parity with 02_LAJ_oracle_loop_gpt.py / run_all_prompt_openai.sh:
- ZEROSHOT vs FEWSHOT: Decided by command (--zeroshot_only / --fewshot_only), not by
  existence of demo files. For "all", the caller runs this script twice (once per flag).
- vLLM COMPATIBILITY: Local API base, threading semaphores, model profiles.
- REASONING TOKENS: Strips <unused94> and <think> from model outputs.
- RECENCY BIAS FIX: Injects demos BEFORE '### SOURCE TEXT'.
- DYNAMIC EVIDENCE: evidence_tag from args or retrieval_settings in JSONL.
"""

import os
# ---- CPU oversubscription guards ----
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("MKL_NUM_THREADS", "2"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", os.getenv("OPENBLAS_NUM_THREADS", "2"))
os.environ.setdefault("NUMEXPR_NUM_THREADS", os.getenv("NUMEXPR_NUM_THREADS", "2"))

import glob
import json
import sys
import time
import re
import argparse
import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tqdm import tqdm
from openai import OpenAI

write_lock = threading.Lock()

# ======================================================
# CONFIG
# ======================================================
PROMPT_VARIATIONS = {
    "def_cot": {"dir": "prompt_comparison_accuracy/prompts_cot/"},
    "def_only": {"dir": "prompt_comparison_accuracy/prompts_def_only/"},
    "baseline": {"dir": "prompt_comparison_accuracy/baseline/"},
}

EXCLUDED_PROMPTS = []
OUTPUT_ROOT = "LAJ_score_outputs"
DEMONSTRATIONS_DIR = "prompt_comparison_accuracy/demonstrations/"
TEST_MODE = False

# ======================================================
# MAPPING
# ======================================================
FILENAME_TO_HUMAN_KEY = {
    "5_factual_acc": "Level of factual accuracy",
}

PROMPTBASE_TO_CRITERION_ID = {
    "5_factual_acc": "reliability_c5_factual_accuracy",
}

CRITERION_ID_TO_DOMAIN = {
    "reliability_c5_factual_accuracy": "reliability",
}

# ======================================================
# HELPERS
# ======================================================
def run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def md5_text(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def md5_json(obj) -> str:
    return md5_text(json.dumps(obj, ensure_ascii=False, sort_keys=True))

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_jsonl(filepath: str):
    rows = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def append_jsonl(filepath: str, record: dict):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with write_lock:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

def read_existing_record_ids(output_path: str) -> set:
    seen = set()
    if not os.path.exists(output_path):
        return seen
    with open(output_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                rid = rec.get("qa_id") or rec.get("record_id")
                if rid:
                    seen.add(str(rid))
            except Exception:
                continue
    return seen

def get_row_id(row: dict) -> str:
    qa_id = row.get("qa_id")
    if qa_id is not None and str(qa_id).strip():
        return str(qa_id).strip()
    trial = row.get("trial_num") or "UNKNOWN"
    qn = row.get("question_num") or row.get("question_num_raw_source", "UNKNOWN")
    return f"{str(trial).strip()}::{qn}"

def row_belongs_to_prompt(row: dict, prompt_base: str) -> bool:
    # RAG pipeline evaluates factual accuracy
    if "retrieval_settings" in row or "retrieved_context" in row:
        if prompt_base == "5_factual_acc":
            return True
    return False

def get_evidence_tag(row: dict) -> str:
    """Build evidence segment from retrieval_settings (mode, reranker, final_k)."""
    settings = row.get("retrieval_settings", {})
    if not settings:
        return "unknown_retrieval"
    mode = settings.get("retrieval_mode", "unknown")
    reranker = settings.get("reranker_type", "none")
    parts = [mode]
    if reranker and reranker != "none":
        parts.append(reranker)
    # Include final_k when present so k3 vs k5 get different paths
    final_k = settings.get("final_k")
    if final_k is not None:
        try:
            parts.append(f"k{int(final_k)}")
        except (TypeError, ValueError):
            pass
    return "_".join(parts)

def is_linked_trial_id(trial: str) -> bool:
    if not trial or not isinstance(trial, str):
        return False
    t = trial.strip().upper()
    if t in ("UNKNOWN", "NULL", "NONE", "NAN", "0"):
        return False
    return bool(re.match(r"^NCT\d+$", t))


def discover_prompts(prompts_dir: str, excluded_list: list):
    if not os.path.exists(prompts_dir):
        return []
    files = glob.glob(os.path.join(prompts_dir, "*.json"))
    files = [p for p in files if os.path.basename(p) not in excluded_list]
    files.sort()
    return files


def extract_version_from_filename(filename: str):
    base_name = os.path.splitext(filename)[0]
    m = re.search(r"_v(\d+)$", base_name)
    return f"v{m.group(1)}" if m else None


def get_base_prompt_name(filename: str):
    base_name = os.path.splitext(filename)[0]
    base_name = re.sub(r"_v\d+$", "", base_name)
    return base_name


# ======================================================
# DEMONSTRATIONS (RECENCY BIAS FIX)
# ======================================================
def load_demonstrations(prompt_name: str):
    demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{prompt_name}_demos.json")
    if os.path.exists(demo_file):
        try:
            with open(demo_file, "r", encoding="utf-8") as f:
                return (json.load(f).get("demonstrations", []), demo_file)
        except Exception:
            pass
    base_name = re.sub(r"_short_v\d+$", "", prompt_name)
    base_name = re.sub(r"_v\d+$", "", base_name)
    if base_name != prompt_name:
        demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{base_name}_demos.json")
        if os.path.exists(demo_file):
            try:
                with open(demo_file, "r", encoding="utf-8") as f:
                    return (json.load(f).get("demonstrations", []), demo_file)
            except Exception:
                pass
    return ([], None)

def format_demonstrations(demos: list) -> str:
    if not demos:
        return ""
    lines = ["\n\n### EXAMPLES\n"]
    for i, demo in enumerate(demos, 1):
        lines.append(f"Example {i}:")
        lines.append(f"Trial: {demo.get('trial_num', 'N/A')}")
        lines.append(f"User Question: {demo.get('question', 'N/A')}")
        lines.append(f"Chatbot Answer: {demo.get('answer', 'N/A')}")
        reas = demo.get("ai_reasoning", "")
        reas = "" if reas is None else str(reas)
        reas_json = json.dumps(reas, ensure_ascii=False)
        score = demo.get("final_score", "N/A")
        lines.append(f"Expected JSON Output: {{\"reasoning\": {reas_json}, \"final_score\": {score}}}\n")
    return "\n".join(lines).strip()

def inject_demos(user_prompt: str, demo_text: str):
    """Injects demos ABOVE the source text to preserve Recency Bias."""
    if not demo_text or not demo_text.strip():
        return (user_prompt, "NO_DEMOS")
    
    anchors = [
        ("### SOURCE TEXT", "SOURCE_TEXT"), 
        ("### OUTPUT (STRICT JSON ONLY)", "OUTPUT_STRICT"),
    ]
    for anchor, label in anchors:
        if anchor in user_prompt:
            idx = user_prompt.find(anchor)
            new_prompt = user_prompt[:idx] + demo_text.strip() + "\n\n" + user_prompt[idx:]
            return (new_prompt, label)
    return (user_prompt + "\n\n" + demo_text.strip(), "APPEND_FALLBACK")

# ======================================================
# vLLM REASONING EXTRACTION
# ======================================================
THOUGHT_RE = re.compile(
    r"<(?:unused94|think|start_of_thought)>\s*(.*?)\s*<(?:unused95|/think|end_of_thought)>",
    flags=re.DOTALL | re.IGNORECASE
)
THOUGHT_UNCLOSED_RE = re.compile(
    r"<(?:unused94|think|start_of_thought)>\s*(.*)\Z",
    flags=re.DOTALL | re.IGNORECASE
)


def parse_reasoning_and_json(raw_text: str) -> tuple[str, str]:
    """Separates <unused94> or <think> reasoning tokens from final JSON."""
    m = THOUGHT_RE.search(raw_text)
    if m:
        reasoning = m.group(1).strip()
        remainder = (raw_text[:m.start()] + raw_text[m.end():]).strip()
        return reasoning, remainder

    m2 = THOUGHT_UNCLOSED_RE.search(raw_text)
    if m2:
        # no closing marker; treat everything after opening as reasoning
        return m2.group(1).strip(), raw_text[:m2.start()].strip()

    return "", raw_text.strip()

def extract_score(text: str) -> int:
    _, clean_json = parse_reasoning_and_json(text)
    if not clean_json:
        clean_json = text
        
    try:
        l, r = clean_json.find("{"), clean_json.rfind("}")
        if l != -1 and r != -1 and r > l:
            obj = json.loads(clean_json[l:r+1])
            if "final_score" in obj:
                return int(obj["final_score"])
    except Exception:
        pass
        
    m = re.search(r'final_score[^0-9]{0,20}(\d)', clean_json, flags=re.IGNORECASE)
    return int(m.group(1)) if m else -1

def build_messages_from_profile(profile: dict, system_text: str, formatted_user_text: str):
    msg_style = profile.get("message_style", "system_user")
    merge_system = bool(profile.get("merge_system_into_user", False))
    
    if merge_system and system_text:
        formatted_user_text = f"{system_text}\n\n{formatted_user_text}".strip()
        system_text = ""

    messages = []
    if msg_style == "system_user":
        if system_text: messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": formatted_user_text})
    elif msg_style == "user_only":
        if system_text and not merge_system:
            formatted_user_text = f"{system_text}\n\n{formatted_user_text}".strip()
        messages.append({"role": "user", "content": formatted_user_text})
    return messages

# ======================================================
# WORKER
# ======================================================
def process_one(job):
    row, prompt_data, prompt_base, prompt_hash, variation, args, client, vllm_sema, model_profile, demo_text, demos_meta = job

    row_id = get_row_id(row)
    trial = str(row.get("trial_num", "UNKNOWN")).strip()
    qn = row.get("question_num")
    question = str(row.get("question", "")).strip()
    answer = str(row.get("chatbot_answer", row.get("answer", ""))).strip()

    # RAG Context
    raw_context = row.get("retrieved_context", "").strip()
    has_context = bool(raw_context)
    
    context_missing_reason = "context_present"
    if not has_context:
        context_missing_reason = "linked_but_missing_context" if is_linked_trial_id(trial) else "unlinked_general"

    template = prompt_data.get("instruction_template", "")
    
    if has_context:
        context_message = raw_context
    else:
        context_message = "" 
        target_header = "### SOURCE TEXT (Informed Consent Form)"
        replacement_header = "### SOURCE TEXT\nNO TRIAL DOCUMENT PROVIDED."
        if target_header in template:
            template = template.replace(target_header, replacement_header)
        else:
            template = re.sub(r"### SOURCE TEXT \(Informed Consent Form\)", replacement_header, template, flags=re.IGNORECASE)

    user_prompt = template.replace("{context}", context_message).replace("{question}", question).replace("{answer}", answer)
    user_prompt, demos_anchor_used = inject_demos(user_prompt, demo_text)

    messages = build_messages_from_profile(
        model_profile, 
        prompt_data.get("system_role", ""), 
        user_prompt
    )

    extra_body = {}
    if "enable_thinking" in model_profile:
        extra_body = {"chat_template_kwargs": {"enable_thinking": bool(model_profile["enable_thinking"])}}

    t0 = time.time()
    try:
        with vllm_sema:
            resp = client.chat.completions.create(
                model=args.model,
                messages=messages,
                temperature=args.temperature,
                top_p=args.top_p,
                max_tokens=args.max_tokens,
                extra_body=extra_body if extra_body else None,
            )
        text = resp.choices[0].message.content
        hidden_thoughts, clean_json = parse_reasoning_and_json(text)
        score = extract_score(text)
        status, err = "ok", None
    except Exception as e:
        hidden_thoughts, clean_json, score, status, err = "", "", -1, "error", str(e)
    dt = time.time() - t0

    return {
        "status": status,
        "qa_id": row_id,
        "record_id": row_id,
        "run_key": md5_text(f"{prompt_hash}::{args.model}::{row_id}"),
        "dataset_id": row.get("dataset_id", args.dataset_id),
        "trial_num": trial,
        "question_num": qn,
        "question": question,
        "chatbot_answer": answer,
        "human_score": row.get("human_score"),
        "criterion_id": PROMPTBASE_TO_CRITERION_ID.get(prompt_base, prompt_base),
        "domain": CRITERION_ID_TO_DOMAIN.get(PROMPTBASE_TO_CRITERION_ID.get(prompt_base), "unknown_domain"),
        "method": "prompt_fewshot" if demos_meta.get("num_demos", 0) > 0 else "prompt_zeroshot",
        "prompt_variation": variation,
        "prompt_file": prompt_data.get("_prompt_filename", ""),
        "prompt_hash": prompt_hash,
        "fewshot_enabled": demos_meta.get("num_demos", 0) > 0,
        "num_demos": demos_meta.get("num_demos", 0),
        "demos_file": demos_meta.get("demos_file"),
        "demos_hash": demos_meta.get("demos_hash"),
        "demos_injection_anchor": demos_anchor_used,
        "has_retrieved_context": has_context,
        "context_missing_reason": context_missing_reason,
        "evidence_tag": get_evidence_tag(row),
        "retrieved_context": context_message,
        "retrieval_meta": row.get("retrieval_meta", []),
        "retrieval_settings": row.get("retrieval_settings", {}),
        "judge_model": args.model,
        "backend": "vllm",
        "model_profile": args.model_profile,
        "vllm_api_base": args.vllm_api_base,
        "generation_config": {"temperature": args.temperature, "top_p": args.top_p, "max_tokens": args.max_tokens},
        "ai_reasoning": hidden_thoughts if hidden_thoughts else clean_json,
        "ai_score": score,
        "parse_error": "Failed to extract integer" if score == -1 and status == "ok" else None,
        "error_msg": err,
        "latency_s": round(dt, 4),
    }

# ======================================================
# MAIN
# ======================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_jsonl", required=True)
    parser.add_argument("--dataset_id", required=True)
    parser.add_argument("--prompt_variation", type=str, default=None)
    parser.add_argument("--prompt_base", type=str, default=None)
    
    parser.add_argument("--vllm_api_base", type=str, default=os.getenv("VLLM_API_BASE", "http://localhost:8000/v1"))
    parser.add_argument("--model", type=str, default=os.getenv("VLLM_MODEL_NAME", "qwen3-30b"))
    parser.add_argument("--model_profiles_json", type=str, default=os.getenv("MODEL_PROFILES_JSON", "configs/model_profiles.json"))
    parser.add_argument("--model_profile", type=str, default=os.getenv("MODEL_PROFILE", "qwen_instruct_vllm"))

    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top_p", type=float, default=1.0)
    parser.add_argument("--max_tokens", type=int, default=1024)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--vllm_inflight", type=int, default=8)
    
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--debug_payload", action="store_true")
    parser.add_argument("--run_stamp", type=str, default=None)
    
    parser.add_argument("--zeroshot_only", action="store_true", help="Run all prompts with no demonstrations (output under method=prompt_zeroshot).")
    parser.add_argument("--fewshot_only", action="store_true", help="Run only prompts that have demonstrations (output under method=prompt_fewshot).")
    parser.add_argument("--test_n", type=int, default=None, help="If set, process only the first N rows (for smoke/test runs).")
    parser.add_argument("--evidence_tag", type=str, default=None, help="Override evidence path segment (e.g. from input filename). If set, used instead of get_evidence_tag(rows[0]).")

    args = parser.parse_args()

    profiles = load_json(args.model_profiles_json)
    model_profile = profiles[args.model_profile]

    client = OpenAI(base_url=args.vllm_api_base, api_key="EMPTY")
    vllm_sema = threading.Semaphore(max(1, args.vllm_inflight))

    rows = load_jsonl(args.input_jsonl)
    if args.test_n is not None:
        rows = rows[: args.test_n]
    elif TEST_MODE:
        rows = rows[:5]

    stamp = args.run_stamp if args.run_stamp else run_stamp()
    if args.evidence_tag is not None and args.evidence_tag.strip():
        global_evidence_tag = args.evidence_tag.strip()
    else:
        global_evidence_tag = get_evidence_tag(rows[0]) if rows else "unknown_evidence"

    variations_to_run = {args.prompt_variation: PROMPT_VARIATIONS[args.prompt_variation]} if args.prompt_variation else PROMPT_VARIATIONS

    for variation, vcfg in variations_to_run.items():
        prompt_paths = discover_prompts(vcfg["dir"], EXCLUDED_PROMPTS)
        
        for prompt_path in prompt_paths:
            prompt_filename = os.path.basename(prompt_path)
            prompt_base = get_base_prompt_name(prompt_filename)
            if args.prompt_base and prompt_base != args.prompt_base: continue
            if prompt_base not in PROMPTBASE_TO_CRITERION_ID: continue

            prompt_data = load_json(prompt_path)
            prompt_data["_prompt_filename"] = prompt_filename
            prompt_hash = md5_json(prompt_data)

            demos, demos_file = load_demonstrations(os.path.splitext(prompt_filename)[0])
            demo_text = format_demonstrations(demos)
            num_demos = len(demos)
            demos_meta = {"num_demos": num_demos, "demos_file": demos_file, "demos_hash": md5_json({"demonstrations": demos}) if demos else None}
            # Decide by command (zeroshot_only/fewshot_only), not by existence of demo files: all variations get both when caller runs twice.
            if args.zeroshot_only:
                demo_text = ""
                method_segment = "prompt_zeroshot"
                demos_meta = {**demos_meta, "num_demos": 0}
            else:
                method_segment = "prompt_fewshot" if num_demos > 0 else "prompt_zeroshot"
            if args.fewshot_only and num_demos == 0: continue
            rows_for_prompt = [r for r in rows if row_belongs_to_prompt(r, prompt_base)]
            if not rows_for_prompt: continue

            criterion_id = PROMPTBASE_TO_CRITERION_ID[prompt_base]
            domain = CRITERION_ID_TO_DOMAIN[criterion_id]
            criterion_name = criterion_id.split("_", 1)[1] if "_" in criterion_id else criterion_id

            out_dir = os.path.join(OUTPUT_ROOT, f"dataset={args.dataset_id}", domain, f"method={method_segment}", "backend=vllm", f"model={args.model}", f"evidence={global_evidence_tag}", f"variation={variation}", f"run={stamp}")
            os.makedirs(out_dir, exist_ok=True)

            ver = extract_version_from_filename(prompt_filename)
            out_path = os.path.join(out_dir, f"{criterion_name}_{ver}.jsonl" if ver else f"{criterion_name}.jsonl")

            seen = read_existing_record_ids(out_path) if not args.force else set()
            jobs = [(r, prompt_data, prompt_base, prompt_hash, variation, args, client, vllm_sema, model_profile, demo_text, demos_meta) for r in rows_for_prompt if get_row_id(r) not in seen]

            if not jobs: continue
            print(f"📝 {prompt_filename} | processing {len(jobs)} records -> {out_path}")

            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = [ex.submit(process_one, j) for j in jobs]
                try:
                    completed = tqdm(as_completed(futures), total=len(futures), leave=False, file=sys.stderr)
                except (OSError, AttributeError):
                    completed = as_completed(futures)
                for fut in completed:
                    append_jsonl(out_path, fut.result())

if __name__ == "__main__":
    main()