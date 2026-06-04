#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
prompt_oracle_loop_robust.py
------------------------------------------------------------
LLM-as-a-Judge evaluation using ORACLE FULL TEXT (ICF).

Updates:
- ORACLE INDEXING: Scans ICF directories once and builds a fast O(1) lookup map.
- ROBUST HEADER REPLACEMENT: Drops the ICF header if the text file is missing.
- CONTEXT DIAGNOSTICS: Logs missing files ("linked_but_missing_file") for error tracking.
- ZERO/FEW-SHOT AGNOSTIC: Automatically detects and injects demonstrations.
"""

import os
import sys
import glob
import json
import time
import re
import argparse
import hashlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from google import genai
from google.genai import types

# ======================================================
# DEFAULT CONFIG
# ======================================================
KEY_FILE = "prompt_evaluation_scripts/gemini_key.txt"
MODEL_NAME = "gemini-3-pro-preview"

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
# Mapping
# ======================================================
FILENAME_TO_HUMAN_KEY = {
    "1_critical_harm": "Presence of critical harms risks",
    "2_individualized_advice": "Presence of individualized medical advice",
    "3_persuasion": "Presence of inappropriate persuasion",
    "4_transparent": "Level of transparency",
    "5_factual_acc": "Level of factual accuracy",
    "6_useful": "Level of usefulness",
    "7_clarity": "Level of clarity and succinctness",
}

PROMPTBASE_TO_CRITERION_ID = {
    "1_critical_harm": "safety_c1_critical_harm",
    "2_individualized_advice": "safety_c2_individualized_medical_advice",
    "3_persuasion": "safety_c3_inappropriate_persuasion",
    "4_transparent": "reliability_c4_transparency",
    "5_factual_acc": "reliability_c5_factual_accuracy",
    "6_useful": "communication_c6_usefulness",
    "7_clarity": "communication_c7_clarity_succinctness",
}

CRITERION_ID_TO_DOMAIN = {
    "safety_c1_critical_harm": "safety",
    "safety_c2_individualized_medical_advice": "safety",
    "safety_c3_inappropriate_persuasion": "safety",
    "reliability_c4_transparency": "reliability",
    "reliability_c5_factual_accuracy": "reliability",
    "communication_c6_usefulness": "communication_quality",
    "communication_c7_clarity_succinctness": "communication_quality",
}

# ======================================================
# Helpers
# ======================================================
def run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def md5_text(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def md5_json(obj) -> str:
    return md5_text(json.dumps(obj, ensure_ascii=False, sort_keys=True))

def load_api_key(filepath: str) -> str:
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read().strip()

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
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def is_valid_completed_record(rec: dict) -> bool:
    if rec.get("status") != "ok":
        return False
    if rec.get("parse_error"):
        return False
    score = rec.get("ai_score")
    try:
        score_int = int(float(score))
    except (TypeError, ValueError):
        return False
    if score_int == -1:
        return False
    return True

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
            except Exception:
                continue
            if not is_valid_completed_record(rec):
                continue
            rid = rec.get("qa_id") or rec.get("record_id")
            if rid:
                seen.add(rid)
    return seen

def discover_prompts(prompts_dir: str, excluded_list):
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

def load_prompt(prompt_path: str) -> dict:
    with open(prompt_path, "r", encoding="utf-8") as f:
        return json.load(f)

def safe_format_template(template: str, **kwargs) -> str:
    escaped = template.replace("{", "{{").replace("}", "}}")
    for key in ("context", "question", "answer"):
        escaped = escaped.replace(f"{{{{{key}}}}}", f"{{{key}}}")
    return escaped.format(**kwargs)

def get_row_id(row: dict) -> str:
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

def is_linked_trial_id(trial: str) -> bool:
    if not trial or not isinstance(trial, str):
        return False
    t = trial.strip().upper()
    if t in ("UNKNOWN", "NULL", "NONE", "NAN", "0"):
        return False
    return bool(re.match(r"^NCT\d+$", t))

# ======================================================
# Oracle Index Builder
# ======================================================
def build_oracle_index(text_folder: str) -> dict:
    """Creates a mapping of {NCT_ID: full_file_path} for O(1) instant lookups."""
    oracle_index = {}
    if not os.path.exists(text_folder):
        print(f"⚠ Warning: Oracle text folder not found at {text_folder}")
        return oracle_index

    for root, _, files in os.walk(text_folder):
        for fn in files:
            if fn.endswith(".txt"):
                # Extract NCT ID (e.g. NCT02403505 from NCT02403505_016.txt)
                match = re.match(r"^(NCT\d+)", fn, flags=re.IGNORECASE)
                if match:
                    trial_id = match.group(1).upper()
                    oracle_index[trial_id] = os.path.join(root, fn)
    
    print(f"📚 Built Oracle Index: Found {len(oracle_index)} unique ICF documents.")
    return oracle_index

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
        trial = demo.get("trial_num", "N/A")
        q = demo.get("question", "N/A")
        a = demo.get("answer", "N/A")
        reasoning = demo.get("ai_reasoning", "")
        score = demo.get("final_score", "N/A")
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = "N/A"
        lines.append(f"Example {i}:")
        lines.append(f"Trial: {trial}")
        lines.append(f"User Question: {q}")
        lines.append(f"Chatbot Answer: {a}")
        reas_escaped = json.dumps(str(reasoning), ensure_ascii=False)
        lines.append(f"Expected JSON Output: {{\"reasoning\": {reas_escaped}, \"final_score\": {score}}}")
        lines.append("")
    return "\n".join(lines).strip()

def inject_demos(user_prompt: str, demo_text: str):
    if not demo_text or not demo_text.strip():
        return (user_prompt, "NO_DEMOS")
        
    anchors = [
        ("### SOURCE TEXT", "SOURCE_TEXT"),                       # For C5
        ("### INTERACTION TO EVALUATE", "INTERACTION_EVAL"),      # For C1-C4, C6-C7
        ("### OUTPUT (STRICT JSON ONLY)", "OUTPUT_STRICT"),       # Fallback
        ("### OUTPUT FORMAT", "OUTPUT_FORMAT"),
        ("### RESPONSE FORMAT", "RESPONSE_FORMAT"),
    ]
    
    for anchor, label in anchors:
        if anchor in user_prompt:
            idx = user_prompt.find(anchor)
            # This slices the string and drops the demos RIGHT BEFORE the Actual Data
            new_prompt = user_prompt[:idx] + demo_text.strip() + "\n\n" + user_prompt[idx:]
            return (new_prompt, label)
            
    return (user_prompt + "\n\n" + demo_text.strip(), "APPEND_FALLBACK")

# ======================================================
# Gemini call
# ======================================================
def cap_prompt_chars(text: str, max_input_chars: int) -> str:
    if max_input_chars <= 0 or len(text) <= max_input_chars:
        return text
    # Keep both lead context and tail format instructions.
    head = int(max_input_chars * 0.7)
    tail = max(0, max_input_chars - head - 24)
    return text[:head] + "\n\n...[TRUNCATED]...\n\n" + text[-tail:]


def _build_gemini_config(max_tokens: int):
    cfg_kwargs = {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_output_tokens": max_tokens,
        "response_mime_type": "application/json",
    }
    try:
        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="LOW")
    except Exception:
        pass
    try:
        cfg_kwargs["safety_settings"] = [
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
            types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
        ]
    except Exception:
        pass
    try:
        return types.GenerateContentConfig(**cfg_kwargs)
    except Exception:
        return types.GenerateContentConfig(top_p=1.0, response_mime_type="application/json")


def call_gemini_json(
    client: genai.Client,
    model: str,
    system_role: str,
    user_prompt: str,
    max_tokens: int,
    max_input_chars: int,
    include_raw: bool,
):
    user_prompt = cap_prompt_chars(user_prompt, max_input_chars)
    if system_role and system_role.strip():
        user_prompt = f"### SYSTEM INSTRUCTIONS\n{system_role.strip()}\n\n### USER TASK\n{user_prompt}"
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])]

    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=model,
            contents=contents,
            config=_build_gemini_config(max_tokens),
        )
        raw = (getattr(resp, "text", "") or "").strip()
        latency = time.time() - t0

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                parsed = parsed[0]
            reasoning = parsed.get("reasoning", "") or raw
            try:
                score = int(parsed.get("final_score", -1))
            except Exception:
                m = re.search(r"final_score[^0-9]{0,10}(\d)", raw, flags=re.IGNORECASE)
                score = int(m.group(1)) if m else -1

            out = {
                "ok": True,
                "ai_reasoning": reasoning,
                "ai_score": score,
                "parse_error": None,
                "error_msg": None,
                "latency_s": latency,
            }
            if include_raw:
                out["judge_output_raw"] = raw
            return out

        except json.JSONDecodeError as e:
            out = {
                "ok": True,
                "ai_reasoning": raw,
                "ai_score": -1,
                "parse_error": str(e),
                "error_msg": None,
                "latency_s": latency,
            }
            if include_raw:
                out["judge_output_raw"] = raw
            return out

    except Exception as e:
        return {
            "ok": False,
            "ai_reasoning": "",
            "ai_score": -1,
            "parse_error": None,
            "error_msg": str(e),
            "latency_s": None,
        }

# ======================================================
# Worker
# ======================================================
def process_one(job):
    row, prompt_data, prompt_base, prompt_hash, variation, max_tokens, max_input_chars, include_raw, client, demo_text, demos_meta, oracle_index, model_name = job

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

    # -------------------------------------------------------------
    # 1. ORACLE CONTEXT FETCHING & DIAGNOSTICS
    # -------------------------------------------------------------
    raw_context = ""
    has_context = False
    context_missing_reason = "unlinked_general"

    if is_linked_trial_id(trial):
        file_path = oracle_index.get(trial.upper())
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_context = f.read().strip("\ufeff").strip()
                has_context = bool(raw_context)
                context_missing_reason = "context_present" if has_context else "linked_but_empty_file"
            except Exception as e:
                context_missing_reason = f"file_read_error: {e}"
        else:
            context_missing_reason = "linked_but_missing_file"

    system_role = prompt_data.get("system_role", "You are a clinical auditor.")
    template = prompt_data.get("instruction_template", "")

    # -------------------------------------------------------------
    # 2. ROBUST HEADER REPLACEMENT
    # -------------------------------------------------------------
    if has_context:
        context_message = raw_context
    else:
        context_message = ""
        
        target_header = "### SOURCE TEXT (Informed Consent Form)"
        replacement_header = "### SOURCE TEXT\nNO TRIAL DOCUMENT PROVIDED."
        
        if target_header in template:
            template = template.replace(target_header, replacement_header)
        else:
            template = re.sub(
                r"### SOURCE TEXT \(Informed Consent Form\)", 
                replacement_header, 
                template, 
                flags=re.IGNORECASE
            )

    try:
        user_prompt = safe_format_template(template, context=context_message, question=question, answer=answer)
    except Exception:
        if has_context:
            user_prompt = f"### SOURCE TEXT (Informed Consent Form)\n{context_message}\n\n### USER QUESTION\n{question}\n\n### CHATBOT ANSWER\n{answer}\n"
        else:
            user_prompt = f"### SOURCE TEXT\nNO TRIAL DOCUMENT PROVIDED.\n\n### USER QUESTION\n{question}\n\n### CHATBOT ANSWER\n{answer}\n"

    user_prompt, demos_anchor_used = inject_demos(user_prompt, demo_text)

    resp = call_gemini_json(
        client=client,
        model=model_name,
        system_role=system_role,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        max_input_chars=max_input_chars,
        include_raw=include_raw,
    )

    criterion_id = PROMPTBASE_TO_CRITERION_ID.get(prompt_base, prompt_base)
    domain = CRITERION_ID_TO_DOMAIN.get(criterion_id, "unknown_domain")

    run_key = md5_text(f"{prompt_hash}::{model_name}::{row_id}")
    human_key = FILENAME_TO_HUMAN_KEY.get(prompt_base)
    human_score = row.get(human_key) if human_key else None

    num_demos = demos_meta.get("num_demos", 0)
    method = "prompt_fewshot" if num_demos > 0 else "prompt_zeroshot"

    out = {
        "status": "ok" if resp["ok"] else "error",
        "qa_id": row_id,
        "record_id": row_id,
        "run_key": run_key,
        "dataset_id": row.get("dataset_id", None),

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
        
        # --- ORACLE Specific Metadata ---
        "has_retrieved_context": has_context,
        "context_missing_reason": context_missing_reason,
        "evidence_tag": "oracle_full_text",
        
        "retrieval_meta": None,
        "retrieval_settings": None,

        "judge_model": model_name,
        "backend": "gemini-3-pro",
        "model_profile": "google_gemini_generate_content",
        "generation_config": {"temperature": 0.0, "top_p": 1.0, "max_tokens": max_tokens, "max_input_chars": max_input_chars},

        "ai_reasoning": resp.get("ai_reasoning", ""),
        "ai_score": resp.get("ai_score", -1),
        "parse_error": resp.get("parse_error", None),
        "error_msg": resp.get("error_msg", None),
        "latency_s": resp.get("latency_s", None),
    }

    if include_raw:
        out["judge_output_raw"] = resp.get("judge_output_raw", "")
    return out

# ======================================================
# MAIN
# ======================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_jsonl", required=True, help="Input evaluation JSONL file")
    parser.add_argument("--dataset_id", required=True, help="Dataset name used in output path (e.g., add_round2)")
    parser.add_argument("--text_folder", type=str, default="ICF_text_cleaned", help="Folder containing full ICF texts")
    parser.add_argument("--max_tokens", type=int, default=768, help="Responses API max_output_tokens")
    parser.add_argument("--max_input_chars", type=int, default=12000, help="Character cap applied to formatted user prompt before API call")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent requests (parallel over records per prompt)")
    parser.add_argument("--force", action="store_true", help="Overwrite (disable resume)")
    parser.add_argument("--include_raw_output", action="store_true", help="Store judge_output_raw")
    parser.add_argument("--run_stamp", type=str, default=None)
    parser.add_argument("--prompt_variation", type=str, default=None)
    parser.add_argument("--prompt_base", type=str, default=None)
    parser.add_argument("--zeroshot_only", action="store_true", help="Run only prompts with no demonstrations")
    parser.add_argument("--fewshot_only", action="store_true", help="Run only prompts that have demonstrations")
    parser.add_argument("--model", type=str, default=MODEL_NAME, help="Gemini model name (e.g. gemini-3-pro-preview)")
    args = parser.parse_args()

    api_key = load_api_key(KEY_FILE) if os.path.isfile(KEY_FILE) else os.environ.get("GEMINI_API_KEY", "").strip()
    client = genai.Client(api_key=api_key) if api_key else genai.Client()

    rows = load_jsonl(args.input_jsonl)
    if TEST_MODE:
        rows = rows[:5]

    stamp = args.run_stamp if args.run_stamp else run_stamp()
    
    # 1. Build the Oracle Index map once before the threading begins
    oracle_index = build_oracle_index(args.text_folder)
    global_evidence_tag = "oracle_full_text"

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

        print(f"\n=== Variation={variation} | Evidence={global_evidence_tag} | Input Rows={len(rows)} ===")

        for prompt_path in prompt_paths:
            prompt_filename = os.path.basename(prompt_path)
            prompt_base = get_base_prompt_name(prompt_filename)

            if args.prompt_base and prompt_base != args.prompt_base:
                continue
            if prompt_base not in PROMPTBASE_TO_CRITERION_ID:
                continue

            prompt_data = load_prompt(prompt_path)
            prompt_data["_prompt_filename"] = prompt_filename
            prompt_hash = md5_json(prompt_data)

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
            # Decide by command (PROMPT_METHOD), not by existence of demo files: all variations get both zeroshot and fewshot when requested.
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
                "backend=gemini-3-pro",
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
                if "dataset_id" not in r:
                    r["dataset_id"] = args.dataset_id
                
                # Pass oracle_index to the worker
                jobs.append((r, prompt_data, prompt_base, prompt_hash, variation, args.max_tokens, args.max_input_chars, args.include_raw_output, client, demo_text, demos_meta, oracle_index, args.model))

            if not jobs:
                print(f"⏩ {prompt_filename}: already complete (resume).")
                continue

            print(f"📝 {prompt_filename} | processing {len(jobs)} records -> {out_path}")

            # Parallel over records (ThreadPoolExecutor)
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futures = [ex.submit(process_one, j) for j in jobs]
                try:
                    completed = tqdm(as_completed(futures), total=len(futures), leave=False, file=sys.stderr)
                except (OSError, AttributeError):
                    completed = as_completed(futures)
                for fut in completed:
                    rec = fut.result()
                    append_jsonl(out_path, rec)

            print(f"✅ Done: {prompt_filename}")

    print("\n✅ ALL COMPLETE.")

if __name__ == "__main__":
    main()
