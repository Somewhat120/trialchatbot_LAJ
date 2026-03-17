#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
run_c5_factual_accuracy_gemini_fulltext.py
------------------------------------------------------------
C5-only agent runner (Gemini API) using FULL_TEXT_ORACLE context.

Design goals:
- Zero changes to the existing multi-criterion agent loop runner (no regression risk)
- Matches the same output record schema and run_key logic style
- Reads a mixed JSONL (rows for all criteria) but filters to C5 only
- Discovers skills only from: skills/reliability/c5_factual_accuracy/SKILL_v*.md
- Loads ICF full text by trial_num (NCT) from a TEXT_FOLDER, like your prompt oracle script
- Replaces {context}, {question}, {answer} in SKILL text for agent prompting
- Resume/dedup using qa_id (preferred) or record_id fallback

Usage example:
python run_c5_factual_accuracy_gemini_fulltext.py \
  --input_jsonl data/mixed.jsonl \
  --dataset_id development \
  --skills_dir skills/reliability/c5_factual_accuracy \
  --text_folder dev_icf_vanilla \
  --judge_model gemini-3-pro-preview \
  --key_file prompt_comparison_script/gemini_key.txt \
  --output_root LAJ_score_outputs \
  --max_tokens 768 \
  --include_raw_output
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

# Ensure repo-root imports work even when launched outside /scratch/yg3191/LAJ.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# DSPy imports for dspy_opt_prompt support
from dspy_engine.utils_paths import resolve_latest_best_skill_path

# ------------------------------------------
# Constants / IDs (C5-specific)
# ------------------------------------------
C5_SKILL_DIR_NAME = "c5_factual_accuracy"
C5_CRITERION_ID = "reliability_c5_factual_accuracy"
C5_DOMAIN = "reliability"
C5_HUMAN_KEY = "Level of factual accuracy"

MAX_API_RETRIES = 2
RETRY_DELAY_SECONDS = 3


# ------------------------------------------
# Helpers
# ------------------------------------------
def now_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_api_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def discover_skills(skills_dir: str, excluded_list: Optional[List[str]] = None) -> List[str]:
    excluded_list = excluded_list or []
    pattern = os.path.join(skills_dir, "SKILL*.md")
    files = glob.glob(pattern)
    files = [p for p in files if os.path.basename(p) not in excluded_list]
    files.sort()
    return files


def extract_version_from_filename(filename: str) -> Optional[str]:
    base_name = os.path.splitext(filename)[0]
    m = re.search(r"_v(\d+)$", base_name)
    return f"v{m.group(1)}" if m else None


def get_qa_id(row: dict) -> Optional[int]:
    qa = row.get("qa_id")
    if qa is None:
        return None
    try:
        return int(float(qa))
    except (ValueError, TypeError):
        return None


def build_record_id(row: dict) -> str:
    trial = str(row.get("trial_num", "UNKNOWN")).strip()
    qa_id = row.get("qa_id")
    if qa_id:
        return f"{trial}::{qa_id}"
    qn = row.get("question_num") or row.get("question_num_raw_source", "UNKNOWN")
    try:
        qn = int(float(qn))
    except Exception:
        qn = str(qn)
    return f"{trial}::{qn}"

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


def read_existing_seen(output_path: str) -> set:
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
            # Prefer qa_id for resume
            qa = rec.get("qa_id")
            if qa is not None:
                try:
                    seen.add(int(float(qa)))
                    continue
                except Exception:
                    pass
            # Fallback record_id
            rid = rec.get("record_id")
            if rid:
                seen.add(rid)
    return seen


def row_is_c5(row: dict) -> bool:
    # Preferred: criterion_id
    if str(row.get("criterion_id", "")).strip() == C5_CRITERION_ID:
        return True
    # Fallback: human key present
    return C5_HUMAN_KEY in row


def get_full_text_by_nct(trial_id: str, text_folder: str) -> Optional[str]:
    trial_id = str(trial_id).strip()
    for root, _, files in os.walk(text_folder):
        for fn in files:
            if fn.startswith(trial_id) and fn.endswith(".txt"):
                path = os.path.join(root, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return f.read().strip("\ufeff")
                except Exception:
                    return None
    return None


def safe_fill_skill(skill_text: str, context: str, question: str, answer: str) -> str:
    # Skills are plain MD with placeholders; this is enough (no .format)
    return (
        skill_text
        .replace("{context}", context)
        .replace("{question}", question)
        .replace("{answer}", answer)
    )


def cap_prompt_chars(text: str, max_input_chars: int) -> str:
    if max_input_chars <= 0 or len(text) <= max_input_chars:
        return text
    head = int(max_input_chars * 0.7)
    tail = max(0, max_input_chars - head - 24)
    return text[:head] + "\n\n...[TRUNCATED]...\n\n" + text[-tail:]


# ------------------------------------------
# Gemini call
# ------------------------------------------
def _build_gemini_config(gen_cfg: Dict[str, Any]):
    cfg_kwargs = {
        "temperature": gen_cfg.get("temperature", 0.0),
        "top_p": gen_cfg.get("top_p", 1.0),
        "max_output_tokens": gen_cfg.get("max_tokens", 768),
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


def call_gemini_json(
    client: genai.Client,
    system_role: str,
    user_text: str,
    model: str,
    gen_cfg: Dict[str, Any],
    max_input_chars: int,
) -> Dict[str, Any]:
    user_text = cap_prompt_chars(user_text, max_input_chars)
    if system_role and system_role.strip():
        user_text = f"### SYSTEM INSTRUCTIONS\n{system_role.strip()}\n\n### USER TASK\n{user_text}"

    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=user_text)])],
            config=_build_gemini_config(gen_cfg),
        )
        latency = time.time() - t0
        raw = (getattr(resp, "text", "") or "").strip()

        # Parse
        try:
            parsed = json.loads(raw)
            reasoning = parsed.get("reasoning", "") or raw
            try:
                score = int(parsed.get("final_score"))
            except Exception:
                m = re.search(r"final_score[^0-9]{0,10}(\d)", raw, re.IGNORECASE)
                score = int(m.group(1)) if m else -1

            return {
                "ai_reasoning": reasoning,
                "ai_score": score,
                "raw_output": raw,
                "parse_error": None,
                "error": None,
                "latency_s": latency,
            }
        except json.JSONDecodeError as je:
            return {
                "ai_reasoning": raw,
                "ai_score": -1,
                "raw_output": raw,
                "parse_error": str(je),
                "error": None,
                "latency_s": latency,
            }

    except Exception as e:
        return {
            "ai_reasoning": "",
            "ai_score": -1,
            "raw_output": "",
            "parse_error": None,
            "error": str(e),
            "latency_s": None,
        }


# ------------------------------------------
# Main processing
# ------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="C5-only agent runner (Gemini full-text oracle)")
    parser.add_argument("--input_jsonl", required=True, help="Mixed JSONL containing all criteria records")
    parser.add_argument("--dataset_id", required=True, help="Dataset ID used in output path (e.g., development)")
    parser.add_argument("--skills_dir", type=str, default="skills/reliability/c5_factual_accuracy", help="C5 skills dir")
    parser.add_argument("--text_folder", type=str, default="ICF_text_cleaned", help="Folder containing NCT*.txt ICF full text")
    parser.add_argument("--judge_model", type=str, default="gemini-3-pro-preview", help="Gemini model name")
    parser.add_argument("--key_file", type=str, default="prompt_comparison_script/gemini_key.txt", help="Gemini key file (optional with Vertex env auth)")
    parser.add_argument("--output_root", type=str, default="LAJ_score_outputs", help="Output root directory")
    parser.add_argument("--force", action="store_true", help="Disable resume and re-run everything")
    parser.add_argument("--test_rows", type=int, default=None, help="Process only first N matching rows")
    parser.add_argument("--excluded_skills", nargs="*", default=[], help="Skill filenames to exclude")
    parser.add_argument("--max_tokens", type=int, default=768, help="Max output tokens")
    parser.add_argument("--max_input_chars", type=int, default=12000, help="Character cap for user prompt before Gemini call (0 disables)")
    parser.add_argument("--include_raw_output", action="store_true", help="Store judge_output_raw")
    parser.add_argument("--run_stamp", type=str, default=None, help="Fixed run stamp (default: auto timestamp)")
    parser.add_argument(
        "--method",
        type=str,
        default="agent",
        choices=["agent", "dspy_opt_prompt"],
        help="Execution engine: agent (default) or dspy_opt_prompt (requires compiled artifacts)",
    )
    parser.add_argument(
        "--compiled_artifacts_root",
        type=str,
        default="dspy_engine/compiled_artifacts",
        help="Root of compiled artifacts (required for method=dspy_opt_prompt)",
    )
    parser.add_argument(
        "--copro_depth",
        type=int,
        default=1,
        help="COPRO depth used at compile (for resolving artifact)",
    )
    parser.add_argument(
        "--copro_candidates",
        type=int,
        default=3,
        help="COPRO candidates used at compile (for skill artifact, dspy_opt_prompt)",
    )
    args = parser.parse_args()

    # Load input rows and filter to C5
    all_rows = load_jsonl(args.input_jsonl)
    rows = [r for r in all_rows if row_is_c5(r)]
    if args.test_rows and args.test_rows > 0:
        rows = rows[: args.test_rows]

    print(f"📂 Input JSONL: {args.input_jsonl}")
    print(f"   Total rows: {len(all_rows)} | C5 rows: {len(rows)}")
    if not rows:
        print("⚠️ No C5 rows found (criterion_id or human key). Exiting.")
        return

    # Discover C5 skills
    skill_files = discover_skills(args.skills_dir, args.excluded_skills)
    if not skill_files:
        print(f"⚠️ No skills found under: {args.skills_dir}")
        return
    print(f"🔍 Skills: {len(skill_files)} found under {args.skills_dir}")

    api_key = load_api_key(args.key_file) if os.path.isfile(args.key_file) else os.environ.get("GEMINI_API_KEY", "").strip()
    client = genai.Client(api_key=api_key) if api_key else genai.Client()

    run_stamp = args.run_stamp if args.run_stamp else now_run_stamp()

    # Output directory matches your loop-runner hierarchy style
    # Structure: dataset={id}/domain/method={method}/backend=gemini-3-pro/model={model}/evidence=icf_oracle_fulltext/variation={method}/run={stamp}
    out_dir = os.path.join(
        args.output_root,
        f"dataset={args.dataset_id}",
        C5_DOMAIN,
        f"method={args.method}",
        "backend=gemini-3-pro",
        f"model={args.judge_model}",
        "evidence=icf_oracle_fulltext",
        f"variation={args.method}",
        f"run={run_stamp}",
    )
    os.makedirs(out_dir, exist_ok=True)
    print(f"📂 Output dir: {out_dir}")
    print(f"   Method: {args.method}")

    gen_cfg = {"temperature": 0.0, "top_p": 1.0, "max_tokens": args.max_tokens}

    # Cache for best_skill.md content (for dspy_opt_prompt)
    best_skill_cache: Dict[Tuple[str, str], Optional[str]] = {}

    for skill_path in skill_files:
        skill_filename = os.path.basename(skill_path)
        skill_version = extract_version_from_filename(skill_filename)
        skill_text = read_text(skill_path)
        skill_hash = md5(skill_text)

        # Resolve best_skill.md for dspy_opt_prompt (once per skill version)
        opt_prompt_skill_text = None
        if args.method == "dspy_opt_prompt" and args.compiled_artifacts_root:
            resolved_version = skill_version if skill_version else "default"
            cache_key = (C5_CRITERION_ID, resolved_version)
            if cache_key not in best_skill_cache:
                best_skill_path = resolve_latest_best_skill_path(
                    compiled_root=args.compiled_artifacts_root,
                    criterion_id=C5_CRITERION_ID,
                    skill_version=resolved_version,
                    judge_model=args.judge_model,
                    depth=args.copro_depth,
                    candidates=args.copro_candidates,
                    rewriter_model=args.judge_model,
                    backend="gemini-3-pro",
                )
                if best_skill_path:
                    try:
                        best_skill_cache[cache_key] = read_text(best_skill_path)
                        print(f"   ✅ Loaded compiled best_skill: {os.path.basename(best_skill_path)}")
                    except Exception as e:
                        best_skill_cache[cache_key] = None
                        print(f"   ⚠️  Failed to load best_skill: {e}")
                else:
                    best_skill_cache[cache_key] = None
                    print(f"   ⚠️  No compiled best_skill found for {C5_CRITERION_ID} {resolved_version}")
            opt_prompt_skill_text = best_skill_cache.get(cache_key)

        out_name = f"{C5_SKILL_DIR_NAME}_{skill_version}.jsonl" if skill_version else f"{C5_SKILL_DIR_NAME}.jsonl"
        out_path = os.path.join(out_dir, out_name)

        # Resume
        seen = read_existing_seen(out_path) if (os.path.exists(out_path) and not args.force) else set()

        print(f"\n📝 Skill: {skill_filename} (version={skill_version or 'default'})")
        print(f"   Output: {out_path}")
        print(f"   Resume: {'ON' if (seen and not args.force) else 'OFF'}")

        processed = 0
        skipped = 0

        for row in rows:
            qa_id = get_qa_id(row)
            record_id = build_record_id(row)

            # Resume key: prefer qa_id, fallback record_id
            if not args.force:
                if qa_id is not None and qa_id in seen:
                    skipped += 1
                    continue
                if qa_id is None and record_id in seen:
                    skipped += 1
                    continue

            trial = str(row.get("trial_num", "UNKNOWN")).strip()
            question = str(row.get("question", "")).strip()
            answer = str(row.get("answer", "")).strip()
            human_score = row.get(C5_HUMAN_KEY) or row.get("human_score")

            # Full-text oracle context
            full_text = get_full_text_by_nct(trial, args.text_folder)
            if not full_text:
                context_message = (
                    "NOTE: No ICF (Informed Consent Form) text is available for this trial. "
                    "Please evaluate factual accuracy using standard general knowledge and clinical trial principles."
                )
                retrieved_context_tag = "NO_ICF_AVAILABLE_GENERAL_KNOWLEDGE"
            else:
                context_message = full_text
                retrieved_context_tag = "FULL_TEXT_ORACLE"

            # Determine which skill text to use
            if args.method == "dspy_opt_prompt" and opt_prompt_skill_text:
                user_skill_text = opt_prompt_skill_text
            else:
                user_skill_text = skill_text

            # Fill placeholders: {context}, {question}, {answer}
            user_text = safe_fill_skill(user_skill_text, context_message, question, answer)

            # Keep system_role empty (matches your loop runner)
            system_role = ""

            # Retry loop (match style)
            result = None
            for attempt in range(MAX_API_RETRIES + 1):
                if args.method == "agent":
                    result = call_gemini_json(
                        client=client,
                        system_role=system_role,
                        user_text=user_text,
                        model=args.judge_model,
                        gen_cfg=gen_cfg,
                        max_input_chars=args.max_input_chars,
                    )
                elif args.method == "dspy_opt_prompt":
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
                        result = call_gemini_json(
                            client=client,
                            system_role=system_role,
                            user_text=user_text,
                            model=args.judge_model,
                            gen_cfg=gen_cfg,
                            max_input_chars=args.max_input_chars,
                        )
                else:
                    result = {"error": f"Unknown method: {args.method}", "ai_score": -1, "raw_output": ""}
                
                need_retry = (
                    (result.get("raw_output") or "") == ""
                    or result.get("parse_error") is not None
                ) and attempt < MAX_API_RETRIES
                if not need_retry:
                    break
                time.sleep(RETRY_DELAY_SECONDS)

            status = "ok" if result and not result.get("error") else "error"

            run_key = md5(f"{skill_hash}|{args.judge_model}|{args.method}|{record_id}")

            # question_num field for consistency
            qnum = row.get("question_num") or row.get("question_num_raw_source")
            try:
                qnum = int(float(qnum)) if qnum is not None else None
            except Exception:
                qnum = qnum

            out_rec = {
                "status": status,
                "record_id": record_id,
                "run_key": run_key,
                "dataset_id": row.get("dataset_id", args.dataset_id),

                "trial_num": trial,
                "question_num": qnum,
                "qa_id": qa_id,
                "question": question,
                "chatbot_answer": answer,
                "human_score": human_score,

                "criterion_id": C5_CRITERION_ID,
                "retrieved_context": retrieved_context_tag,
                "method": args.method,

                "skill_path": skill_path,
                "skill_filename": skill_filename,
                "skill_version": skill_version,
                "skill_hash": skill_hash,

                "judge_model": args.judge_model,
                "backend": "gemini-3-pro",
                "model_profile": "google_gemini_generate_content",
                "vllm_api_base": None,

                "generation_config": gen_cfg,

                "ai_reasoning": result.get("ai_reasoning", "") if result else "",
                "ai_score": result.get("ai_score", -1) if result else -1,
                "parse_error": result.get("parse_error") if result else "No result",
                "error_msg": result.get("error") if result else "No result",
                "latency_s": result.get("latency_s") if result else None,
            }

            if args.include_raw_output:
                out_rec["judge_output_raw"] = result.get("raw_output", "") if result else ""
            else:
                out_rec["judge_output_raw"] = None

            append_jsonl(out_path, out_rec)
            processed += 1

            # Update resume cache
            if qa_id is not None:
                seen.add(qa_id)
            else:
                seen.add(record_id)

        print(f"   ✅ Done: processed={processed}, skipped={skipped}")

    print("\n✅ ALL COMPLETE.")


if __name__ == "__main__":
    main()
