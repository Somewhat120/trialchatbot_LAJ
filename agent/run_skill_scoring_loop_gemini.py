#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Judge scoring runner (Loop Mode) - Gemini backend
- Processes all skills from a single mixed JSONL file
- Automatically discovers all skill directories and versions
- Filters rows by criterion_id or human label key
- Output structure matches OpenAI / vLLM agent runners
- Backend: Google Gemini API (google-genai)
- Execution engine: agent | dspy_noopt | dspy_opt_prompt
"""

import argparse
import json
import os
import time
import hashlib
import re
import glob
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from google import genai
from google.genai import types
from dspy_engine.utils_paths import resolve_latest_best_skill_path

# Retry when API returns empty or invalid score (transient failures)
MAX_API_RETRIES = 2
RETRY_DELAY_SECONDS = 3

# ==========================================
# MAPPINGS (same as OpenAI / vLLM runners)
# ==========================================

SKILL_DIR_TO_CRITERION_ID = {
    "c1_critical_harm": "safety_c1_critical_harm",
    "c2_individualized_medical_advice": "safety_c2_individualized_medical_advice",
    "c3_inappropriate_persuasion": "safety_c3_inappropriate_persuasion",
    "c4_transparency": "reliability_c4_transparency",
    "c6_usefulness": "communication_c6_usefulness",
    "c7_clarity": "communication_c7_clarity_succinctness",
}

SKILL_DIR_TO_HUMAN_KEY = {
    "c1_critical_harm": "Presence of critical harms risks",
    "c2_individualized_medical_advice": "Presence of individualized medical advice",
    "c3_inappropriate_persuasion": "Presence of inappropriate persuasion",
    "c4_transparency": "Level of transparency",
    "c6_usefulness": "Level of usefulness",
    "c7_clarity": "Level of clarity and succinctness",
}

CRITERION_ID_TO_DOMAIN = {
    "safety_c1_critical_harm": "safety",
    "safety_c2_individualized_medical_advice": "safety",
    "safety_c3_inappropriate_persuasion": "safety",
    "reliability_c4_transparency": "reliability",
    "communication_c6_usefulness": "communication_quality",
    "communication_c7_clarity_succinctness": "communication_quality",
}

# ==========================================
# UTILITIES
# ==========================================

def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records

def append_jsonl(path: str, obj: Dict[str, Any]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def load_api_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

def now_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def cap_prompt_chars(text: str, max_input_chars: int) -> str:
    if max_input_chars <= 0 or len(text) <= max_input_chars:
        return text
    head = int(max_input_chars * 0.7)
    tail = max(0, max_input_chars - head - 24)
    return text[:head] + "\n\n...[TRUNCATED]...\n\n" + text[-tail:]

# ==========================================
# SKILL DISCOVERY
# ==========================================

def discover_skills(skills_dir: str, excluded_list: Optional[List[str]] = None) -> List[str]:
    if excluded_list is None:
        excluded_list = []
    pattern = os.path.join(skills_dir, "SKILL*.md")
    files = glob.glob(pattern)
    filtered = [f for f in files if os.path.basename(f) not in excluded_list]
    filtered.sort()
    return filtered

def discover_all_skill_dirs(skills_root: str) -> List[Tuple[str, str, str]]:
    skill_dirs = []
    for domain in ["safety", "reliability", "communication_quality"]:
        domain_path = os.path.join(skills_root, domain)
        if not os.path.isdir(domain_path):
            continue
        for item in os.listdir(domain_path):
            item_path = os.path.join(domain_path, item)
            if os.path.isdir(item_path):
                skill_dirs.append((item_path, item, domain))
    return sorted(skill_dirs)

def extract_version_from_filename(filename: str) -> Optional[str]:
    base_name = os.path.splitext(filename)[0]
    match = re.search(r'_v(\d+)$', base_name)
    if match:
        return f"v{match.group(1)}"
    return None

# ==========================================
# ROW FILTERING
# ==========================================

def row_belongs_to_skill(row: dict, skill_dir_name: str) -> bool:
    want_cid = SKILL_DIR_TO_CRITERION_ID.get(skill_dir_name)
    row_cid = row.get("criterion_id")
    if want_cid and row_cid:
        return str(row_cid) == str(want_cid)

    human_key = SKILL_DIR_TO_HUMAN_KEY.get(skill_dir_name)
    if human_key and (human_key in row):
        return True

    return False

# ==========================================
# SCORE EXTRACTION (regex fallback, same as vLLM runner)
# ==========================================

_FINAL_SCORE_RE = re.compile(r"final_score[^0-9]{0,20}(\d)", flags=re.IGNORECASE)

def extract_score(text: str) -> Tuple[int, Optional[str]]:
    s = safe_str(text).strip()
    if not s:
        return (-1, "empty_response")
    parse_error = None
    try:
        l = s.find("{")
        r = s.rfind("}")
        if l != -1 and r != -1 and r > l:
            sub = s[l:r + 1]
            obj = json.loads(sub)
            if isinstance(obj, dict) and "final_score" in obj:
                return (int(obj["final_score"]), None)
    except Exception as e:
        parse_error = str(e)

    m = _FINAL_SCORE_RE.search(s)
    if m:
        return (int(m.group(1)), None)

    return (-1, parse_error or "no_score_found")

# ==========================================
# ANTHROPIC CALL (AGENT ENGINE)
# ==========================================

def _build_gemini_config(gen_cfg: Dict[str, Any]):
    cfg_kwargs: Dict[str, Any] = {
        "temperature": gen_cfg.get("temperature", 0.0),
        "top_p": gen_cfg.get("top_p", 1.0),
        "max_output_tokens": gen_cfg.get("max_tokens", 2048),
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
        return types.GenerateContentConfig(top_p=gen_cfg.get("top_p", 1.0), response_mime_type="application/json")


def call_gemini(
    client: genai.Client,
    system_role: str,
    user_text: str,
    model: str,
    gen_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    t0 = time.time()
    try:
        max_input_chars = int(gen_cfg.get("max_input_chars", 0) or 0)
        user_text = cap_prompt_chars(user_text, max_input_chars)
        if system_role and system_role.strip():
            user_text = f"### SYSTEM INSTRUCTIONS\n{system_role.strip()}\n\n### USER TASK\n{user_text}"

        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_text)])],
            config=_build_gemini_config(gen_cfg),
        )
        latency = time.time() - t0
        raw = (getattr(response, "text", "") or "").strip()

        score, parse_error = extract_score(raw)
        ai_reasoning = raw
        try:
            l = raw.find("{")
            r = raw.rfind("}")
            if l != -1 and r != -1 and r > l:
                obj = json.loads(raw[l:r + 1])
                ai_reasoning = obj.get("reasoning", raw)
        except Exception:
            pass

        out: Dict[str, Any] = {
            "ai_reasoning": ai_reasoning,
            "ai_score": score,
            "raw_output": raw,
            "latency_s": latency,
        }
        if parse_error:
            out["parse_error"] = parse_error
        return out

    except Exception as e:
        return {
            "error": str(e),
            "ai_reasoning": "",
            "ai_score": -1,
            "raw_output": "",
            "latency_s": None,
        }

# ==========================================
# PROCESS SKILL VERSION WITH ROWS
# ==========================================

def process_skill_version_with_rows(
    skill_path: str,
    skill_version: Optional[str],
    skill_dir_name: str,
    criterion_id: str,
    human_label_key: str,
    rows: List[Dict[str, Any]],
    client: genai.Client,
    dspy_lm: Optional[Any],
    judge_model: str,
    method: str,
    seen_run_keys: set,
    output_path: str,
    force: bool,
    gen_cfg: Dict[str, Any],
    include_raw_output: bool,
    *,
    compiled_artifacts_root: Optional[str] = None,
    best_skill_cache: Optional[Dict[Tuple[str, str], Optional[str]]] = None,
    copro_depth: int = 1,
    copro_candidates: int = 3,
) -> None:
    """
    Process a single skill version with pre-filtered rows.
    """
    skill_text = read_text(skill_path)
    skill_hash = md5(skill_text)
    skill_filename = os.path.basename(skill_path)

    # Resolve and cache best_skill.md content for dspy_opt_prompt (once per criterion+version)
    opt_prompt_skill_text = None
    if method == "dspy_opt_prompt" and compiled_artifacts_root and best_skill_cache is not None:
        resolved_version = skill_version if skill_version else "default"
        cache_key = (criterion_id, resolved_version)
        if cache_key not in best_skill_cache:
            best_skill_path = resolve_latest_best_skill_path(
                compiled_root=compiled_artifacts_root,
                criterion_id=criterion_id,
                skill_version=resolved_version,
                judge_model=judge_model,
                depth=copro_depth,
                candidates=copro_candidates,
                rewriter_model=judge_model,
                backend="gemini-3-pro",
            )
            if best_skill_path:
                try:
                    best_skill_cache[cache_key] = read_text(best_skill_path)
                except Exception as e:
                    best_skill_cache[cache_key] = None
                    print(f"      Warning: Failed to load best_skill for {criterion_id} {resolved_version}: {e}")
            else:
                best_skill_cache[cache_key] = None
                print(f"      Warning: No compiled best_skill for {criterion_id} {resolved_version}")
        opt_prompt_skill_text = best_skill_cache.get(cache_key)

    # Resume support (same as OpenAI / vLLM runners)
    if os.path.exists(output_path) and not force:
        for line in open(output_path, "r", encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    if "run_key" in rec:
                        seen_run_keys.add(rec["run_key"])
                except Exception:
                    continue

    system_role = ""

    for row in rows:
        trial = str(row.get("trial_num", "UNKNOWN"))

        # Normalize question_num (handle None/invalid values)
        qnum_raw = row.get("question_num") or row.get("question_num_raw_source")
        try:
            qnum = int(float(qnum_raw)) if qnum_raw is not None else None
        except (ValueError, TypeError):
            qnum = None
        if qnum is None:
            qnum = "UNKNOWN"

        qa_id = row.get("qa_id", None)
        record_id = f"{trial}::{qnum}"

        # Use qa_id (unique per row) in run_key to avoid dedup collisions
        row_id = str(qa_id) if qa_id is not None else record_id
        run_key = md5(f"{skill_hash}|{judge_model}|{method}|{row_id}")

        if (not force) and (run_key in seen_run_keys):
            continue

        question = safe_str(row.get("question") or row.get("questions") or "")
        answer = safe_str(row.get("answer") or row.get("chatbot_answer") or row.get("response") or "")
        human_score = row.get(human_label_key)

        # Short-circuit empty Q/A
        if not question.strip() or not answer.strip():
            out_rec = {
                "status": "error",
                "record_id": record_id,
                "run_key": run_key,
                "qa_id": qa_id,
                "trial_num": trial,
                "question_num": qnum,
                "question": question,
                "chatbot_answer": answer,
                "human_score": human_score,
                "criterion_id": criterion_id,
                "retrieved_context": "NO_RAG_CONTROL",
                "method": method,
                "skill_path": skill_path,
                "skill_filename": skill_filename,
                "skill_version": skill_version,
                "skill_hash": skill_hash,
                "judge_model": judge_model,
                "backend": "gemini-3-pro",
                "model_profile": "google_gemini_generate_content",
                "vllm_api_base": None,
                "generation_config": gen_cfg,
                "ai_reasoning": "",
                "ai_score": -1,
                "judge_output_raw": "",
                "parse_error": None,
                "error_msg": "empty_question_or_answer",
                "latency_s": None,
            }
            append_jsonl(output_path, out_rec)
            seen_run_keys.add(run_key)
            continue

        # Call judge with retries
        result = None
        ai_reasoning = ""
        for attempt in range(MAX_API_RETRIES + 1):
            if method == "agent":
                user_text = skill_text.replace("{question}", question).replace("{answer}", answer)
                result = call_gemini(
                    client=client,
                    system_role=system_role,
                    user_text=user_text,
                    model=judge_model,
                    gen_cfg=gen_cfg,
                )
                ai_reasoning = result.get("ai_reasoning", result.get("raw_output", ""))
            elif method == "dspy_noopt":
                user_text = skill_text.replace("{question}", question).replace("{answer}", answer)
                result = call_gemini(
                    client=client,
                    system_role=system_role,
                    user_text=user_text,
                    model=judge_model,
                    gen_cfg=gen_cfg,
                )
                ai_reasoning = result.get("ai_reasoning", result.get("raw_output", ""))
            elif method == "dspy_opt_prompt":
                if opt_prompt_skill_text is None:
                    result = {
                        "error": "No compiled best_skill for this criterion/version",
                        "ai_score": -1,
                        "raw_output": "",
                        "ai_reasoning": "",
                        "parse_error": None,
                        "latency_s": None,
                    }
                else:
                    user_text_opt = opt_prompt_skill_text.replace("{question}", question).replace("{answer}", answer)
                    result = call_gemini(
                        client=client,
                        system_role=system_role,
                        user_text=user_text_opt,
                        model=judge_model,
                        gen_cfg=gen_cfg,
                    )
                    ai_reasoning = result.get("ai_reasoning", result.get("raw_output", ""))
            else:
                result = {
                    "error": f"Unknown method: {method}",
                    "ai_score": -1,
                    "raw_output": "",
                    "ai_reasoning": "",
                    "parse_error": None,
                    "latency_s": None,
                }

            need_retry = (
                (result.get("raw_output") or "") == "" or (result.get("ai_score", -1) == -1)
            ) and attempt < MAX_API_RETRIES

            if not need_retry:
                break
            time.sleep(RETRY_DELAY_SECONDS)

        # Status: error if exception OR score extraction failed
        status = "ok"
        if (result.get("error") is not None) or (result.get("ai_score", -1) == -1):
            status = "error"

        parse_error = result.get("parse_error")
        if result.get("ai_score", -1) == -1 and not parse_error:
            parse_error = "no_score_found"

        out_rec = {
            "status": status,
            "record_id": record_id,
            "run_key": run_key,
            "qa_id": qa_id,

            "trial_num": trial,
            "question_num": qnum,
            "question": question,
            "chatbot_answer": answer,
            "human_score": human_score,

            "criterion_id": criterion_id,
            "retrieved_context": "NO_RAG_CONTROL",
            "method": method,

            "skill_path": skill_path,
            "skill_filename": skill_filename,
            "skill_version": skill_version,
            "skill_hash": skill_hash,

            "judge_model": judge_model,
            "backend": "gemini-3-pro",
            "model_profile": "google_gemini_generate_content",
            "vllm_api_base": None,

            "generation_config": gen_cfg,

            "ai_reasoning": ai_reasoning,
            "ai_score": result.get("ai_score", -1),
            "judge_output_raw": result.get("raw_output", "") if include_raw_output else None,
            "parse_error": parse_error,
            "error_msg": result.get("error"),
            "latency_s": result.get("latency_s"),
        }

        append_jsonl(output_path, out_rec)
        seen_run_keys.add(run_key)

# ==========================================
# MAIN
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Judge scoring runner (Loop Mode) - Gemini backend"
    )
    parser.add_argument("--input_jsonl", required=True, help="Mixed JSONL file with all criteria")
    parser.add_argument("--skills_root", type=str, default="skills", help="Root directory containing skill domains")
    parser.add_argument("--dataset_id", type=str, required=True, help="Dataset ID for output path")
    parser.add_argument("--judge_model", type=str, default="gemini-3-pro-preview", help="Gemini model name")
    parser.add_argument("--key_file", type=str, default="prompt_comparison_script/gemini_key.txt", help="Gemini API key file path (optional with Vertex env auth)")
    parser.add_argument("--output_root", type=str, default="LAJ_score_outputs", help="Output root directory")
    parser.add_argument("--force", action="store_true", help="Ignore resume/dedup and re-run")
    parser.add_argument("--test_rows", type=int, default=None, help="Limit processing to first N rows (for testing)")
    parser.add_argument("--excluded_skills", nargs="*", default=[], help="Skill filenames to exclude (e.g., SKILL_v0.md)")
    parser.add_argument("--max_tokens", type=int, default=2048, help="Max output tokens")
    parser.add_argument("--max_input_chars", type=int, default=12000, help="Character cap for user prompt before Gemini call (0 disables)")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature")
    parser.add_argument("--top_p", type=float, default=1.0, help="Top-p")
    parser.add_argument("--include_raw_output", action="store_true", help="Store judge_output_raw in records")
    parser.add_argument(
        "--method",
        type=str,
        default="agent",
        choices=["agent", "dspy_noopt", "dspy_opt_prompt"],
        help="Scoring method: agent, dspy_noopt, or dspy_opt_prompt (DSPy + COPRO rewritten SKILL)",
    )
    parser.add_argument(
        "--compiled_artifacts_root",
        type=str,
        default="dspy_engine/compiled_artifacts",
        help="Root of compiled artifacts (for method=dspy_opt_prompt)",
    )
    parser.add_argument("--copro_depth", type=int, default=1, help="COPRO depth used at compile (for resolving best_skill)")
    parser.add_argument("--copro_candidates", type=int, default=3, help="COPRO candidates used at compile (for resolving best_skill)")
    parser.add_argument("--run_stamp", type=str, default=None, help="Fixed run stamp for resume capability (default: auto-generated timestamp)")
    args = parser.parse_args()

    print(f"Loading mixed JSONL: {args.input_jsonl}")
    all_rows = load_jsonl(args.input_jsonl)
    print(f"   Loaded {len(all_rows)} total rows")

    if args.test_rows and args.test_rows > 0:
        all_rows = all_rows[:args.test_rows]
        print(f"TEST MODE: Processing only first {len(all_rows)} rows")

    print(f"\nDiscovering skills from: {args.skills_root}")
    skill_dirs = discover_all_skill_dirs(args.skills_root)
    print(f"   Found {len(skill_dirs)} skill directories")
    if not skill_dirs:
        print("Warning: No skill directories found!")
        return

    api_key = load_api_key(args.key_file) if os.path.isfile(args.key_file) else os.environ.get("GEMINI_API_KEY", "").strip()
    client = genai.Client(api_key=api_key) if api_key else genai.Client()
    print(f"\nGemini client initialized.")
    print(f"   Judge model: {args.judge_model}")

    run_stamp = args.run_stamp if args.run_stamp else now_run_stamp()
    gen_cfg = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "max_input_chars": args.max_input_chars,
    }

    # Show output path pattern
    example_domain = skill_dirs[0][2] if skill_dirs else "domain"
    out_path_example = os.path.join(
        args.output_root,
        f"dataset={args.dataset_id}",
        example_domain,
        f"method={args.method}",
        "backend=gemini-3-pro",
        f"model={args.judge_model}",
        "evidence=no_rag_control",
        f"variation={args.method}",
        f"run={run_stamp}",
    )
    print(f"\nMethod: {args.method}  ->  outputs under .../method={args.method}/variation={args.method}/run={run_stamp}/")
    print(f"   Example: {out_path_example}\n")

    total_processed = 0
    total_skipped = 0
    best_skill_cache: Dict[Tuple[str, str], Optional[str]] = {} if args.method == "dspy_opt_prompt" else {}

    for skill_dir_path, skill_dir_name, domain in skill_dirs:
        criterion_id = SKILL_DIR_TO_CRITERION_ID.get(skill_dir_name)
        if not criterion_id:
            print(f"Warning: Skipping {skill_dir_name}: no criterion_id mapping")
            total_skipped += 1
            continue

        human_key = SKILL_DIR_TO_HUMAN_KEY.get(skill_dir_name)
        if not human_key:
            print(f"Warning: Skipping {skill_dir_name}: no human_label_key mapping")
            total_skipped += 1
            continue

        rows_for_skill = [r for r in all_rows if row_belongs_to_skill(r, skill_dir_name)]
        if not rows_for_skill:
            print(f"Skipping {skill_dir_name}: 0 matching rows")
            total_skipped += 1
            continue

        print(f"\n{'='*70}")
        print(f"Processing: {skill_dir_name}")
        print(f"   Criterion: {criterion_id}")
        print(f"   Domain: {domain}")
        print(f"   Matching rows: {len(rows_for_skill)}")
        print(f"   Method: {args.method}")

        skill_files = discover_skills(skill_dir_path, args.excluded_skills)
        if not skill_files:
            print(f"   Warning: No skill files found in {skill_dir_path}")
            total_skipped += 1
            continue

        print(f"   Skill versions: {len(skill_files)}")

        out_dir = os.path.join(
            args.output_root,
            f"dataset={args.dataset_id}",
            domain,
            f"method={args.method}",
            "backend=gemini-3-pro",
            f"model={args.judge_model}",
            "evidence=no_rag_control",
            f"variation={args.method}",
            f"run={run_stamp}",
        )
        os.makedirs(out_dir, exist_ok=True)

        seen_run_keys = set()
        for skill_file in skill_files:
            skill_filename = os.path.basename(skill_file)
            skill_version = extract_version_from_filename(skill_filename)

            output_filename = f"{skill_dir_name}_{skill_version}.jsonl" if skill_version else f"{skill_dir_name}.jsonl"
            output_path = os.path.join(out_dir, output_filename)

            print(f"   -> {skill_filename} (version: {skill_version or 'default'}) -> {output_filename}")

            process_skill_version_with_rows(
                skill_path=skill_file,
                skill_version=skill_version,
                skill_dir_name=skill_dir_name,
                criterion_id=criterion_id,
                human_label_key=human_key,
                rows=rows_for_skill,
                client=client,
                dspy_lm=None,
                judge_model=args.judge_model,
                method=args.method,
                seen_run_keys=seen_run_keys,
                output_path=output_path,
                force=args.force,
                gen_cfg=gen_cfg,
                include_raw_output=args.include_raw_output,
                compiled_artifacts_root=args.compiled_artifacts_root if args.method == "dspy_opt_prompt" else None,
                best_skill_cache=best_skill_cache if args.method == "dspy_opt_prompt" else None,
                copro_depth=args.copro_depth,
                copro_candidates=args.copro_candidates,
            )

            total_processed += 1

        print(f"   Done: {skill_dir_name}")

    print(f"\n{'='*70}")
    print("All done!")
    print(f"   Processed: {total_processed} skill versions")
    print(f"   Skipped: {total_skipped} skill directories")
    print(f"   Output root: {args.output_root}/dataset={args.dataset_id}/")
    print(f"   Run stamp: {run_stamp}")

if __name__ == "__main__":
    main()
