#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
02_LAJ_noRAG_loop.py (vLLM no-context)
------------------------------------------------------------
Unified No-Context LAJ runner via vLLM OpenAI-compatible API.
Evaluates criteria like Clarity, Usefulness, etc., without ICF text.

Parity with 02_LAJ_noRAG_loop_gpt.py / run_all_prompt_openai.sh:
- ZEROSHOT vs FEWSHOT: Decided by command (--zeroshot_only / --fewshot_only), not by
  existence of demo files. For "all", the caller runs this script twice (once per flag).
- DYNAMIC FOLDERS: Routes to method=prompt_zeroshot or method=prompt_fewshot.
- OUTPUT PARITY: JSON schema matches the GPT no-context structure.
"""

import os                                                 
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("MKL_NUM_THREADS", "2"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", os.getenv("OPENBLAS_NUM_THREADS", "2"))
os.environ.setdefault("NUMEXPR_NUM_THREADS", os.getenv("NUMEXPR_NUM_THREADS", "2"))

import json
import re
import sys
import glob
import time
import argparse
import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tqdm import tqdm
from openai import OpenAI

# ---------------- thread-safety ----------------
write_lock = threading.Lock()

# ======================================================
# DEFAULT CONFIG
# ======================================================
PROMPT_VARIATIONS = {
    "def_cot": {"dir": "prompt_comparison_folder/prompts_cot/"},
    "def_only": {"dir": "prompt_comparison_folder/prompts_def_only/"},
    "baseline": {"dir": "prompt_comparison_folder/baseline/"},
}

EXCLUDED_PROMPTS = [
    "5_factual_acc.json", 
    "5_factual_acc_v1.json",
    "5_factual_acc_v2.json"
]

OUTPUT_ROOT = "LAJ_score_outputs"
DEMONSTRATIONS_DIR = "prompt_comparison_folder/demonstrations/"
TEST_MODE = False

# ======================================================
# Mapping
# ======================================================
FILENAME_TO_HUMAN_KEY = {
    "1_critical_harm": "Presence of critical harms risks",
    "2_individualized_advice": "Presence of individualized medical advice",
    "3_persuasion": "Presence of inappropriate persuasion",
    "4_transparent": "Level of transparency",
    "6_useful": "Level of usefulness",
    "7_clarity": "Level of clarity and succinctness",
}

PROMPTBASE_TO_CRITERION_ID = {
    "1_critical_harm": "safety_c1_critical_harm",
    "2_individualized_advice": "safety_c2_individualized_medical_advice",
    "3_persuasion": "safety_c3_inappropriate_persuasion",
    "4_transparent": "reliability_c4_transparency",
    "6_useful": "communication_c6_usefulness",
    "7_clarity": "communication_c7_clarity_succinctness",
}

CRITERION_ID_TO_DOMAIN = {
    "safety_c1_critical_harm": "safety",
    "safety_c2_individualized_medical_advice": "safety",
    "safety_c3_inappropriate_persuasion": "safety",
    "reliability_c4_transparency": "reliability",
    "communication_c6_usefulness": "communication_quality",
    "communication_c7_clarity_succinctness": "communication_quality",
}

# ======================================================
# Helpers
# ======================================================
def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def md5_text(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def md5_json(obj) -> str:
    return md5_text(json.dumps(obj, ensure_ascii=False, sort_keys=True))

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_prompt(prompt_path: str) -> dict:
    with open(prompt_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_jsonl(filepath: str) -> List[Dict]:
    rows = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

def append_jsonl(filepath: str, record: Dict) -> None:
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
                    seen.add(rid)
            except Exception:
                continue
    return seen

def discover_prompts(prompts_dir: str, excluded_list: List[str]):
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

def safe_format_template(template: str, **kwargs) -> str:
    escaped = template.replace("{", "{{").replace("}", "}}")
    for key in ("question", "answer"):
        escaped = escaped.replace(f"{{{{{key}}}}}", f"{{{key}}}")
    return escaped.format(**kwargs)

def get_row_id(row: dict) -> str:
    """Canonical row id: qa_id when present, else trial::question_num fallback."""
    qa_id = row.get("qa_id")
    if qa_id is not None and str(qa_id).strip():
        return str(qa_id).strip()
    trial = row.get("trial_num") or "UNKNOWN"
    trial = str(trial).strip()
    qn = row.get("question_num") or row.get("question_num_raw_source", "UNKNOWN")
    try:
        qn = int(float(qn))
    except Exception:
        qn = str(qn)
    return f"{trial}::{qn}"

def row_belongs_to_prompt(row: dict, prompt_base: str) -> bool:
    want_cid = PROMPTBASE_TO_CRITERION_ID.get(prompt_base)
    row_cid = row.get("criterion_id")
    if want_cid and row_cid and str(row_cid) == str(want_cid):
        return True
    human_key = FILENAME_TO_HUMAN_KEY.get(prompt_base)
    if human_key and (human_key in row):
        return True
    return False

# ======================================================
# Demonstrations (few-shot)
# ======================================================
def load_demonstrations(prompt_name: str):
    demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{prompt_name}_demos.json")
    if os.path.exists(demo_file):
        try:
            with open(demo_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return (data.get("demonstrations", []), demo_file)
        except Exception as e:
            print(f"⚠ Warning: Could not load demonstrations from {demo_file}: {e}")
            
    base_name = re.sub(r"_short_v\d+$", "", prompt_name)
    base_name = re.sub(r"_v\d+$", "", base_name)
    if base_name != prompt_name:
        demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{base_name}_demos.json")
        if os.path.exists(demo_file):
            try:
                with open(demo_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return (data.get("demonstrations", []), demo_file)
            except Exception as e:
                print(f"⚠ Warning: Could not load demonstrations from {demo_file}: {e}")
    return ([], None)

def format_demonstrations(demos: list) -> str:
    if not demos:
        return ""
    lines = ["\n\n### EXAMPLES\n"]
    for i, demo in enumerate(demos, 1):
        q = demo.get("question", "N/A")
        a = demo.get("answer", "N/A")
        reasoning = demo.get("ai_reasoning", "")
        score = demo.get("final_score", "N/A")
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = "N/A"
            
        reasoning = "" if reasoning is None else str(reasoning)
        reas_escaped = json.dumps(reasoning, ensure_ascii=False)

        lines.append(f"Example {i}:")
        lines.append(f"User Question: {q}")
        lines.append(f"Chatbot Answer: {a}")
        lines.append(f"Expected JSON Output: {{\"reasoning\": {reas_escaped}, \"final_score\": {score}}}")
        lines.append("")
    return "\n".join(lines).strip()

def inject_demos(user_prompt: str, demo_text: str):
    if not demo_text or not demo_text.strip():
        return (user_prompt, "NO_DEMOS")
    anchors = [
        ("### OUTPUT (STRICT JSON ONLY)", "OUTPUT_STRICT"),
        ("### OUTPUT FORMAT", "OUTPUT_FORMAT"),
        ("### RESPONSE FORMAT", "RESPONSE_FORMAT"),
    ]
    for anchor, label in anchors:
        if anchor in user_prompt:
            idx = user_prompt.find(anchor)
            new_prompt = user_prompt[:idx] + demo_text.strip() + "\n\n" + user_prompt[idx:]
            return (new_prompt, label)
    return (user_prompt + "\n\n" + demo_text.strip(), "APPEND_FALLBACK")

# ------------------------------------------------
# Profile & Score Extraction Helpers
# ------------------------------------------------
def load_model_profiles(path: str) -> dict:
    profiles = load_json(path)
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"Model profiles file must be a non-empty dict: {path}")
    return profiles

def build_messages_from_profile(
    profile: dict,
    system_text: str,
    formatted_user_text: str
) -> List[Dict[str, Any]]:
    """Constructs API payload based on open-source model requirements."""
    merge_system = bool(profile.get("merge_system_into_user", False))

    if merge_system and system_text:
        combined_user = f"{system_text}\n\n{formatted_user_text}".strip()
        system_text = ""
    else:
        combined_user = formatted_user_text

    content_style = profile.get("content_style", "string")
    if content_style == "openai_text_list":
        user_content = [{"type": "text", "text": combined_user}]
        system_content = [{"type": "text", "text": system_text}] if system_text else None
    else:
        user_content = combined_user
        system_content = system_text if system_text else None

    msg_style = profile.get("message_style", "system_user")
    messages: List[Dict[str, Any]] = []

    if msg_style == "system_user":
        if system_content:
            messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": user_content})
    elif msg_style == "user_only":
        if system_content and not merge_system:
            if content_style == "openai_text_list":
                user_content = [{"type": "text", "text": f"{system_text}\n\n{combined_user}".strip()}]
            else:
                user_content = f"{system_text}\n\n{combined_user}".strip()
        messages.append({"role": "user", "content": user_content})
    else:
        raise ValueError(f"Unknown message_style in profile: {msg_style}")

    return messages

_FINAL_SCORE_RE = re.compile(r'final_score[^0-9]{0,20}(\d)', flags=re.IGNORECASE)

def extract_score(text: str) -> Tuple[int, Optional[str]]:
    s = safe_str(text).strip()

    # --- Strip out the thinking block safely ---
    s = re.sub(r'<think>.*?</think>', '', s, flags=re.DOTALL | re.IGNORECASE).strip()
    # ------------------------------------------------

    if not s:
        return (-1, "empty_response")
    try:
        l = s.find("{")
        r = s.rfind("}")
        if l != -1 and r != -1 and r > l:
            sub = s[l:r+1]
            obj = json.loads(sub)
            if isinstance(obj, dict) and "final_score" in obj:
                return (int(obj["final_score"]), None)
    except json.JSONDecodeError as e:
        parse_error = str(e)
    except Exception as e:
        parse_error = str(e)
    
    m = _FINAL_SCORE_RE.search(s)
    if m:
        return (int(m.group(1)), None)
    
    return (-1, "no_score_found")

# ======================================================
# Worker
# ======================================================
def process_one(job):
    row, prompt_data, prompt_base, prompt_hash, variation, args, client, vllm_sema, model_profile, demo_text, demos_meta = job

    row_id = get_row_id(row)
    trial = str(row.get("trial_num", "UNKNOWN")).strip()
    
    qn = row.get("question_num") or row.get("question_num_raw_source")
    if qn is not None:
        try:
            qn = int(float(qn))
        except Exception:
            pass

    question = str(row.get("question", "")).strip()
    answer = str(row.get("chatbot_answer", row.get("answer", ""))).strip()

    system_role = prompt_data.get("system_role", "You are a clinical auditor.")
    template = prompt_data.get("instruction_template", "")

    # Safe formatting (no context variable)
    try:
        user_prompt = safe_format_template(template, question=question, answer=answer)
    except Exception:
        user_prompt = f"### USER QUESTION\n{question}\n\n### CHATBOT ANSWER\n{answer}\n"

    # Inject Demonstrations
    user_prompt, demos_anchor_used = inject_demos(user_prompt, demo_text)

    # Build Final Messages for vLLM
    messages = build_messages_from_profile(
        profile=model_profile,
        system_text=system_role,
        formatted_user_text=user_prompt
    )

    criterion_id = PROMPTBASE_TO_CRITERION_ID.get(prompt_base, prompt_base)
    domain = CRITERION_ID_TO_DOMAIN.get(criterion_id, "unknown_domain")

    run_key = md5_text(f"{prompt_hash}::{args.model}::{row_id}")
    human_key = FILENAME_TO_HUMAN_KEY.get(prompt_base)
    human_score = row.get(human_key) if human_key else None

    num_demos = demos_meta.get("num_demos", 0)
    method = "prompt_fewshot" if num_demos > 0 else "prompt_zeroshot"

    # Per-profile thinking control (for Qwen3/Qwen3.5, etc.)
    extra_body: Dict[str, Any] = {}
    if "enable_thinking" in model_profile:
        extra_body = {
            "chat_template_kwargs": {
                "enable_thinking": bool(model_profile["enable_thinking"])
            }
        }

    # Call vLLM API
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
        score, parse_error = extract_score(text)
        status = "ok" if score != -1 else "error"
        err = parse_error if score == -1 else None
    except Exception as e:
        text = ""
        score = -1
        status = "error"
        parse_error = None
        err = f"{e}"
    dt = time.time() - t0

    out = {
        "status": status,
        "qa_id": row_id,
        "record_id": row_id,
        "run_key": run_key,
        "dataset_id": row.get("dataset_id", args.dataset_id),

        "trial_num": trial,
        "question_num": qn,
        "question": question,
        "chatbot_answer": answer,
        "human_score": human_score,

        "criterion_id": criterion_id,
        "domain": domain,

        "method": method,
        "prompt_variation": variation,
        "prompt_file": prompt_data.get("_prompt_filename", ""),
        "prompt_hash": prompt_hash,

        "fewshot_enabled": num_demos > 0,
        "num_demos": num_demos,
        "demos_file": demos_meta.get("demos_file"),
        "demos_hash": demos_meta.get("demos_hash"),
        "demos_injection_anchor": demos_anchor_used,
        
        "evidence_tag": "no_context",

        "judge_model": args.model,
        "backend": "vllm",
        "model_profile": args.model_profile,
        "vllm_api_base": args.vllm_api_base,
        "generation_config": {
            "temperature": args.temperature, 
            "top_p": args.top_p, 
            "max_tokens": args.max_tokens
        },

        "ai_reasoning": text,
        "ai_score": score,
        "parse_error": parse_error,
        "error_msg": err,
        "latency_s": round(dt, 4),
    }

    if args.debug_payload:
        user_msg = messages[-1].get("content", "") if messages else ""
        user_preview = user_msg
        if isinstance(user_msg, list):
            user_preview = (user_msg[0].get("text", "") if user_msg else "")
        out["debug"] = {
            "messages_hash": md5_text(json.dumps(messages, ensure_ascii=False)),
            "user_preview": safe_str(user_preview)[:600],
        }

    return out

# ======================================================
# MAIN
# ======================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_jsonl", required=True, help="Retrieval JSONL output file")
    parser.add_argument("--dataset_id", required=True, help="Dataset name used in output path")
    parser.add_argument("--prompt_variation", type=str, default=None)
    parser.add_argument("--prompt_base", type=str, default=None)
    parser.add_argument("--prompt_file", type=str, default=None, help="If set, run only this prompt filename (e.g. 7_clarity_v1.json).")
    
    # vLLM Specs
    parser.add_argument("--vllm_api_base", type=str, default=os.getenv("VLLM_API_BASE", "http://localhost:8000/v1"))
    parser.add_argument("--model", type=str, default=os.getenv("VLLM_MODEL_NAME", "qwen3-30b"))
    parser.add_argument("--model_profiles_json", type=str, default=os.getenv("MODEL_PROFILES_JSON", "configs/model_profiles.json"))
    parser.add_argument("--model_profile", type=str, default=os.getenv("MODEL_PROFILE", "qwen_instruct_vllm"))

    # Generation Specs
    parser.add_argument("--temperature", type=float, default=float(os.getenv("TEMPERATURE", "0.0")))
    parser.add_argument("--top_p", type=float, default=float(os.getenv("TOP_P", "1.0")))
    parser.add_argument("--max_tokens", type=int, default=int(os.getenv("MAX_NEW_TOKENS", "768")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("MAX_WORKERS", "16")))
    parser.add_argument("--vllm_inflight", type=int, default=int(os.getenv("VLLM_INFLIGHT", "8")))
    
    parser.add_argument("--force", action="store_true", help="Overwrite (disable resume)")
    parser.add_argument("--debug_payload", action="store_true", help="Store raw messages debug")
    parser.add_argument("--quiet", action="store_true", help="Suppress terminal output")
    parser.add_argument("--run_stamp", type=str, default=None)
    parser.add_argument("--zeroshot_only", action="store_true", help="Run all prompts with no demonstrations (output under method=prompt_zeroshot).")
    parser.add_argument("--fewshot_only", action="store_true", help="Run only prompts that have demonstrations (output under method=prompt_fewshot).")
    parser.add_argument("--test_n", type=int, default=None, help="If set, process only the first N rows (for smoke/test runs).")

    args = parser.parse_args()

    # Model profiles
    profiles = load_model_profiles(args.model_profiles_json)
    if args.model_profile not in profiles:
        raise RuntimeError(f"Unknown model_profile={args.model_profile}. Available: {list(profiles.keys())}")
    model_profile = profiles[args.model_profile]

    # vLLM client
    client = OpenAI(base_url=args.vllm_api_base, api_key="EMPTY")
    vllm_sema = threading.Semaphore(max(1, int(args.vllm_inflight)))

    rows = load_jsonl(args.input_jsonl)
    if args.test_n is not None:
        rows = rows[: args.test_n]
    elif TEST_MODE:
        rows = rows[:5]

    stamp = args.run_stamp if args.run_stamp else run_stamp()
    
    # Force evidence_tag for no-context models
    global_evidence_tag = "no_context"

    if args.prompt_variation and args.prompt_variation not in PROMPT_VARIATIONS:
        print(f"⚠ Unknown --prompt_variation={args.prompt_variation}")
        return
    variations_to_run = (
        {args.prompt_variation: PROMPT_VARIATIONS[args.prompt_variation]}
        if args.prompt_variation
        else PROMPT_VARIATIONS
    )

    for variation, vcfg in variations_to_run.items():
        prompts_dir = vcfg["dir"]
        prompt_paths = discover_prompts(prompts_dir, EXCLUDED_PROMPTS)
        if not prompt_paths:
            continue

        if not args.quiet:
            print(f"\n=== Variation={variation} | Evidence={global_evidence_tag} | Input Rows={len(rows)} ===")

        for prompt_path in prompt_paths:
            prompt_filename = os.path.basename(prompt_path)
            prompt_base = get_base_prompt_name(prompt_filename)

            if args.prompt_base and prompt_base != args.prompt_base:
                continue
            if args.prompt_file and prompt_filename != args.prompt_file:
                continue
            if prompt_base not in PROMPTBASE_TO_CRITERION_ID:
                continue

            prompt_data = load_prompt(prompt_path)
            prompt_data["_prompt_filename"] = prompt_filename
            prompt_hash = md5_json(prompt_data)

            # Load Demonstrations
            prompt_name = os.path.splitext(prompt_filename)[0]
            demos, demos_file = load_demonstrations(prompt_name)
            demo_text = format_demonstrations(demos)
            num_demos = len(demos)
            demos_hash = md5_json({"demonstrations": demos}) if demos else None
            demos_meta = {
                "num_demos": num_demos,
                "demos_file": demos_file,
                "demos_hash": demos_hash,
            }
            # Decide by command (zeroshot_only/fewshot_only), not by existence of demo files: all variations get both when caller runs twice.
            if args.zeroshot_only:
                demo_text = ""
                method_segment = "prompt_zeroshot"
                demos_meta = {**demos_meta, "num_demos": 0}
            else:
                method_segment = "prompt_fewshot" if num_demos > 0 else "prompt_zeroshot"
            if args.fewshot_only and num_demos == 0:
                continue

            rows_for_prompt = [r for r in rows if row_belongs_to_prompt(r, prompt_base)]
            if not rows_for_prompt:
                continue

            criterion_id = PROMPTBASE_TO_CRITERION_ID.get(prompt_base, prompt_base)
            domain = CRITERION_ID_TO_DOMAIN.get(criterion_id, "unknown_domain")

            criterion_name = criterion_id
            if "_" in criterion_id:
                parts = criterion_id.split("_", 1)
                if len(parts) > 1:
                    criterion_name = parts[1] 

            out_dir = os.path.join(
                OUTPUT_ROOT,
                f"dataset={args.dataset_id}",
                domain,
                f"method={method_segment}",
                "backend=vllm",
                f"model={args.model}",
                f"evidence={global_evidence_tag}",
                f"variation={variation}",
                f"run={stamp}",
            )
            os.makedirs(out_dir, exist_ok=True)

            ver = extract_version_from_filename(prompt_filename)
            out_name = f"{criterion_name}_{ver}.jsonl" if ver else f"{criterion_name}.jsonl"
            out_path = os.path.join(out_dir, out_name)

            seen = set()
            if os.path.exists(out_path) and (not args.force):
                seen = read_existing_record_ids(out_path)

            jobs = []
            for r in rows_for_prompt:
                rid = get_row_id(r)
                if (rid in seen) and (not args.force):
                    continue
                jobs.append((r, prompt_data, prompt_base, prompt_hash, variation, args, client, vllm_sema, model_profile, demo_text, demos_meta))

            if not jobs:
                if not args.quiet:
                    print(f"⏩ {prompt_filename}: already complete (resume).")
                continue

            if not args.quiet:
                print(f"📝 {prompt_filename} | processing {len(jobs)} records -> {out_path}")

            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = [ex.submit(process_one, j) for j in jobs]
                try:
                    completed = tqdm(as_completed(futures), total=len(futures), leave=False, disable=args.quiet, desc=f"vLLM:{variation}:{prompt_filename}", file=sys.stderr)
                except (OSError, AttributeError):
                    completed = as_completed(futures)
                for fut in completed:
                    rec = fut.result()
                    append_jsonl(out_path, rec)

            if not args.quiet:
                print(f"✅ Done: {prompt_filename}")

    if not args.quiet:
        print("\n✅ ALL COMPLETE.")

if __name__ == "__main__":
    main()