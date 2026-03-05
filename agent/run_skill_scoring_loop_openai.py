#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Judge scoring runner (Loop Mode)
- Processes all skills from a single mixed JSONL file
- Automatically discovers all skill directories and versions
- Filters rows by criterion_id or human label key
- Output structure matches prompt comparison script
- Backend: OpenAI Responses API
- Execution engine: agent | dspy_noopt | dspy_opt_signature | dspy_opt_prompt
  (dspy_opt_signature = DSPy + COPRO compiled signature; dspy_opt_prompt = DSPy + COPRO rewritten SKILL text)
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

import httpx
from openai import OpenAI

# DSPy (noopt / opt_signature) engine (opt_demos disabled for fair comparison)
from dspy_engine.lm_openai_responses import OpenAIResponsesLM
from dspy_engine.run_dspy_openai_noopt import call_dspy_openai_noopt
from dspy_engine.utils_paths import resolve_latest_best_skill_path
from dspy_engine.archive.run_dspy_openai_opt_signature import (
    resolve_latest_artifact_path,
    load_optimized_signature,
    call_dspy_openai_opt_signature,
)

# Retry when API returns empty or invalid JSON (transient failures)
MAX_API_RETRIES = 2
RETRY_DELAY_SECONDS = 3

# ==========================================
# MAPPINGS (similar to prompt script)
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

def get_qa_id(row: dict) -> Optional[int]:
    """Extract qa_id as the unique identifier."""
    qa = row.get("qa_id")
    if qa is None:
        return None
    try:
        return int(float(qa))
    except (ValueError, TypeError):
        return None

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

def load_api_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

def now_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

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
# OPENAI CALL (AGENT ENGINE)
# ==========================================

def call_openai(
    client: OpenAI,
    system_role: str,
    user_text: str,
    model: str,
    gen_cfg: Dict[str, Any],
    openai_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    messages = []
    if system_role and system_role.strip():
        messages.append({
            "role": "system",
            "content": [{"type": "input_text", "text": system_role}]
        })
    messages.append({
        "role": "user",
        "content": [{"type": "input_text", "text": user_text}]
    })

    t0 = time.time()
    try:
        kwargs = {
            "model": model,
            "input": messages,
            "text": {
                "format": {"type": "json_object"} if openai_cfg.get("json_object", True) else None,
                "verbosity": openai_cfg.get("verbosity", "medium"),
            },
            "reasoning": {"effort": openai_cfg.get("reasoning_effort", "low")},
            "store": openai_cfg.get("store", False),
            "max_output_tokens": gen_cfg.get("max_tokens", 256),
        }

        if not model.startswith("gpt-5"):
            kwargs["temperature"] = gen_cfg.get("temperature", 0.0)
            kwargs["top_p"] = gen_cfg.get("top_p", 1.0)

        response = client.responses.create(**kwargs)
        latency = time.time() - t0

        raw = response.output_text or ""

        try:
            parsed = json.loads(raw)
            reasoning = parsed.get("reasoning", raw)
            try:
                ai_score = int(parsed.get("final_score"))
            except Exception:
                m = re.search(r'final_score[^0-9]{0,10}(\d)', raw, re.IGNORECASE)
                ai_score = int(m.group(1)) if m else -1

            return {
                "ai_reasoning": reasoning,
                "ai_score": ai_score,
                "raw_output": raw,
                "latency_s": latency,
            }

        except json.JSONDecodeError as je:
            return {
                "ai_reasoning": raw,
                "ai_score": -1,
                "raw_output": raw,
                "parse_error": str(je),
                "latency_s": latency,
            }

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
    client: OpenAI,
    dspy_lm: OpenAIResponsesLM,
    judge_model: str,
    method: str,
    output_path: str,
    force: bool,
    gen_cfg: Dict[str, Any],
    openai_cfg: Dict[str, Any],
    include_raw_output: bool,
    *,
    compiled_artifacts_root: Optional[str] = None,
    opt_sig_cache: Optional[Dict[Tuple[str, str], Any]] = None,
    best_skill_cache: Optional[Dict[Tuple[str, str], Optional[str]]] = None,
    copro_depth: int = 1,
    copro_breadth: int = 3,
    copro_candidates: int = 3,
) -> None:
    """
    Process a single skill version with pre-filtered rows.
    """
    skill_text = read_text(skill_path)
    skill_hash = md5(skill_text)
    skill_filename = os.path.basename(skill_path)

    # Resolve and cache optimized signature for dspy_opt_signature (once per criterion+version)
    opt_sig = None
    if method == "dspy_opt_signature" and compiled_artifacts_root and opt_sig_cache is not None:
        resolved_version = skill_version if skill_version else "default"
        cache_key = (criterion_id, resolved_version)
        if cache_key not in opt_sig_cache:
            artifact_path = resolve_latest_artifact_path(
                compiled_root=compiled_artifacts_root,
                criterion_id=criterion_id,
                skill_version=resolved_version,
                model=judge_model,
                depth=copro_depth,
                breadth=copro_breadth,
            )
            if artifact_path:
                try:
                    opt_sig_cache[cache_key] = load_optimized_signature(artifact_path, criterion_id)
                except Exception as e:
                    opt_sig_cache[cache_key] = None
                    print(f"      ⚠️  Failed to load opt signature for {criterion_id} {resolved_version}: {e}")
            else:
                opt_sig_cache[cache_key] = None
                print(f"      ⚠️  No compiled artifact for {criterion_id} {resolved_version}")
        opt_sig = opt_sig_cache.get(cache_key)

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
            )
            if best_skill_path:
                try:
                    best_skill_cache[cache_key] = read_text(best_skill_path)
                except Exception as e:
                    best_skill_cache[cache_key] = None
                    print(f"      ⚠️  Failed to load best_skill for {criterion_id} {resolved_version}: {e}")
            else:
                best_skill_cache[cache_key] = None
                print(f"      ⚠️  No compiled best_skill for {criterion_id} {resolved_version}")
        opt_prompt_skill_text = best_skill_cache.get(cache_key)

    # Resume support (use qa_id as unique identifier)
    seen_qa_ids = set()
    if os.path.exists(output_path) and not force:
        for line in open(output_path, "r", encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    qa = rec.get("qa_id")
                    if qa is not None:
                        try:
                            seen_qa_ids.add(int(float(qa)))
                        except (ValueError, TypeError):
                            continue
                except Exception:
                    continue

    for row in rows:
        qa_id = get_qa_id(row)
        if qa_id is not None and (qa_id in seen_qa_ids) and (not force):
            continue

        trial = str(row.get("trial_num", "UNKNOWN"))
        qnum = row.get("question_num") or row.get("question_num_raw_source")
        try:
            qnum = int(float(qnum)) if qnum is not None else None
        except (ValueError, TypeError):
            qnum = None
        if qnum is None:
            qnum = "UNKNOWN"

        record_id = f"{trial}::{qnum}"

        # Include method in run_key to avoid cross-method collisions if outputs are merged later.
        run_key = md5(f"{skill_hash}|{judge_model}|{method}|{record_id}")

        question = str(row.get("question", ""))
        answer = str(row.get("answer", ""))
        human_score = row.get(human_label_key)

        system_role = ""

        # Call judge with retries
        result = None
        for attempt in range(MAX_API_RETRIES + 1):
            if method == "agent":
                user_text = skill_text.replace("{question}", question).replace("{answer}", answer)
                result = call_openai(
                    client=client,
                    system_role=system_role,
                    user_text=user_text,
                    model=judge_model,
                    gen_cfg=gen_cfg,
                    openai_cfg=openai_cfg,
                )
            elif method == "dspy_noopt":
                # DSPy uses raw SKILL.md and separate question/answer fields
                result = call_dspy_openai_noopt(
                    lm=dspy_lm,
                    skill_text=skill_text,
                    question=question,
                    answer=answer,
                    criterion_id=criterion_id,
                )
            elif method == "dspy_opt_demos":
                result = {"error": "dspy_opt_demos disabled (few-shot not used for fair comparison)", "ai_score": -1, "raw_output": ""}
            elif method == "dspy_opt_signature":
                if opt_sig is None:
                    result = {
                        "error": "No compiled signature for this criterion/version",
                        "ai_score": -1,
                        "raw_output": "",
                        "ai_reasoning": "",
                        "parse_error": None,
                        "latency_s": None,
                    }
                else:
                    result = call_dspy_openai_opt_signature(
                        lm=dspy_lm,
                        skill_text=skill_text,
                        question=question,
                        answer=answer,
                        criterion_id=criterion_id,
                        optimized_sig=opt_sig,
                    )
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
                    result = call_dspy_openai_noopt(
                        lm=dspy_lm,
                        skill_text=opt_prompt_skill_text,
                        question=question,
                        answer=answer,
                        criterion_id=criterion_id,
                    )
            else:
                result = {"error": f"Unknown method: {method}", "ai_score": -1, "raw_output": ""}

            need_retry = (
                (result.get("raw_output") or "") == ""
                or result.get("parse_error") is not None
            ) and attempt < MAX_API_RETRIES

            if not need_retry:
                break
            time.sleep(RETRY_DELAY_SECONDS)

        status = "ok" if (result and ("error" not in result or not result.get("error"))) else "error"

        out_rec = {
            "status": status,
            "record_id": record_id,
            "run_key": run_key,

            "trial_num": trial,
            "question_num": qnum,
            "qa_id": qa_id,
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
            "backend": "openai",
            "model_profile": "openai_gpt5_responses",
            "vllm_api_base": None,

            "generation_config": gen_cfg,

            "ai_reasoning": result.get("ai_reasoning", "") if result else "",
            "ai_score": result.get("ai_score", -1) if result else -1,
            "parse_error": result.get("parse_error") if result else "No result",
            "error_msg": result.get("error") if result else "No result",
            "latency_s": result.get("latency_s") if result else None,
        }

        if include_raw_output:
            out_rec["judge_output_raw"] = result.get("raw_output", "") if result else ""
        else:
            out_rec["judge_output_raw"] = None

        append_jsonl(output_path, out_rec)
        if qa_id is not None:
            seen_qa_ids.add(qa_id)

# ==========================================
# MAIN
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Judge scoring runner (Loop Mode) - Process all skills from mixed JSONL"
    )
    parser.add_argument("--input_jsonl", required=True, help="Mixed JSONL file with all criteria")
    parser.add_argument("--skills_root", type=str, default="skills", help="Root directory containing skill domains")
    parser.add_argument("--dataset_id", type=str, required=True, help="Dataset ID for output path")
    parser.add_argument("--judge_model", type=str, default="gpt-5-nano", help="Judge model name")
    parser.add_argument("--key_file", type=str, default="prompt_comparison_script/gpt_key.txt", help="API key file path")
    parser.add_argument("--output_root", type=str, default="LAJ_score_outputs", help="Output root directory")
    parser.add_argument("--force", action="store_true", help="Ignore resume/dedup and re-run")
    parser.add_argument("--test_rows", type=int, default=None, help="Limit processing to first N rows (for testing)")
    parser.add_argument("--excluded_skills", nargs="*", default=[], help="List of skill filenames to exclude (e.g., SKILL_v0.md)")
    parser.add_argument("--max_tokens", type=int, default=768, help="Max output tokens")
    parser.add_argument("--include_raw_output", action="store_true", help="Store judge_output_raw in records")
    parser.add_argument(
        "--method",
        type=str,
        default="agent",
        choices=["agent", "dspy_noopt", "dspy_opt_signature", "dspy_opt_prompt"],
        help="Execution engine: agent, dspy_noopt, dspy_opt_signature (DSPy + COPRO signature), dspy_opt_prompt (DSPy + COPRO rewritten SKILL)",
    )
    parser.add_argument(
        "--compiled_artifacts_root",
        type=str,
        default="dspy_engine/compiled_artifacts",
        help="Root of compiled artifacts (for method=dspy_opt_signature or dspy_opt_prompt)",
    )
    parser.add_argument("--copro_depth", type=int, default=1, help="COPRO depth used at compile (for resolving artifact)")
    parser.add_argument("--copro_breadth", type=int, default=3, help="COPRO breadth used at compile (for signature artifact)")
    parser.add_argument("--copro_candidates", type=int, default=3, help="COPRO candidates used at compile (for skill artifact, dspy_opt_prompt)")
    parser.add_argument("--run_stamp", type=str, default=None, help="Fixed run stamp for resume capability (default: auto-generated timestamp)")
    args = parser.parse_args()

    print(f"📂 Loading mixed JSONL: {args.input_jsonl}")
    all_rows = load_jsonl(args.input_jsonl)
    print(f"   Loaded {len(all_rows)} total rows")

    if args.test_rows and args.test_rows > 0:
        all_rows = all_rows[:args.test_rows]
        print(f"🧪 TEST MODE: Processing only first {len(all_rows)} rows")

    print(f"\n🔍 Discovering skills from: {args.skills_root}")
    skill_dirs = discover_all_skill_dirs(args.skills_root)
    print(f"   Found {len(skill_dirs)} skill directories")

    if not skill_dirs:
        print("⚠️  No skill directories found!")
        return

    api_key = load_api_key(args.key_file)
    http_client = httpx.Client(verify=False)
    client = OpenAI(api_key=api_key, http_client=http_client)

    run_stamp = args.run_stamp if args.run_stamp else now_run_stamp()

    # Show method and output path pattern so user can confirm (e.g. dspy_opt_demos vs dspy_noopt)
    example_domain = skill_dirs[0][2] if skill_dirs else "domain"
    out_path_example = os.path.join(
        args.output_root,
        f"dataset={args.dataset_id}",
        example_domain,
        f"method={args.method}",
        "backend=openai",
        f"model={args.judge_model}",
        "evidence=no_rag_control",
        f"variation={args.method}",
        f"run={run_stamp}",
    )
    print(f"\n📂 Method: {args.method}  →  outputs under .../method={args.method}/variation={args.method}/run={run_stamp}/")
    print(f"   Example: {out_path_example}\n")

    gen_cfg = {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": args.max_tokens,
    }

    openai_cfg = {
        "json_object": True,
        "verbosity": "medium",
        "reasoning_effort": "low",
        "store": False,
    }

    # Instantiate DSPy LM (used when method is dspy_noopt, dspy_opt_signature, or dspy_opt_prompt)
    dspy_lm = OpenAIResponsesLM(
        client=client,
        model=args.judge_model,
        max_output_tokens=args.max_tokens,
        temperature=gen_cfg["temperature"],
        top_p=gen_cfg["top_p"],
        json_object=True,
        verbosity=openai_cfg["verbosity"],
        reasoning_effort=openai_cfg["reasoning_effort"],
        store=openai_cfg["store"],
    )

    total_processed = 0
    total_skipped = 0

    for skill_dir_path, skill_dir_name, domain in skill_dirs:
        criterion_id = SKILL_DIR_TO_CRITERION_ID.get(skill_dir_name)
        if not criterion_id:
            print(f"⚠️  Skipping {skill_dir_name}: no criterion_id mapping")
            total_skipped += 1
            continue

        human_key = SKILL_DIR_TO_HUMAN_KEY.get(skill_dir_name)
        if not human_key:
            print(f"⚠️  Skipping {skill_dir_name}: no human_label_key mapping")
            total_skipped += 1
            continue

        rows_for_skill = [r for r in all_rows if row_belongs_to_skill(r, skill_dir_name)]
        if not rows_for_skill:
            print(f"⏩ {skill_dir_name}: 0 matching rows, skipping")
            total_skipped += 1
            continue

        print(f"\n{'='*70}")
        print(f"📝 Processing: {skill_dir_name}")
        print(f"   Criterion: {criterion_id}")
        print(f"   Domain: {domain}")
        print(f"   Matching rows: {len(rows_for_skill)}")
        print(f"   Method: {args.method}")

        skill_files = discover_skills(skill_dir_path, args.excluded_skills)
        if not skill_files:
            print(f"   ⚠️  No skill files found in {skill_dir_path}")
            total_skipped += 1
            continue

        print(f"   Skill versions: {len(skill_files)}")

        # Match prompt runner: dataset at top, then domain/method/backend/model/evidence/variation/run
        out_dir = os.path.join(
            args.output_root,
            f"dataset={args.dataset_id}",
            domain,
            f"method={args.method}",
            "backend=openai",
            f"model={args.judge_model}",
            "evidence=no_rag_control",
            f"variation={args.method}",
            f"run={run_stamp}",
        )
        os.makedirs(out_dir, exist_ok=True)

        opt_sig_cache: Dict[Tuple[str, str], Any] = {} if args.method == "dspy_opt_signature" else {}
        best_skill_cache: Dict[Tuple[str, str], Optional[str]] = {} if args.method == "dspy_opt_prompt" else {}
        for skill_file in skill_files:
            skill_filename = os.path.basename(skill_file)
            skill_version = extract_version_from_filename(skill_filename)

            base_name = skill_dir_name
            output_filename = f"{base_name}_{skill_version}.jsonl" if skill_version else f"{base_name}.jsonl"
            output_path = os.path.join(out_dir, output_filename)

            print(f"   → {skill_filename} (version: {skill_version or 'default'}) -> {output_filename}")

            process_skill_version_with_rows(
                skill_path=skill_file,
                skill_version=skill_version,
                skill_dir_name=skill_dir_name,
                criterion_id=criterion_id,
                human_label_key=human_key,
                rows=rows_for_skill,
                client=client,
                dspy_lm=dspy_lm,
                judge_model=args.judge_model,
                method=args.method,
                output_path=output_path,
                force=args.force,
                gen_cfg=gen_cfg,
                openai_cfg=openai_cfg,
                include_raw_output=args.include_raw_output,
                compiled_artifacts_root=args.compiled_artifacts_root if args.method in ("dspy_opt_signature", "dspy_opt_prompt") else None,
                opt_sig_cache=opt_sig_cache if args.method == "dspy_opt_signature" else None,
                best_skill_cache=best_skill_cache if args.method == "dspy_opt_prompt" else None,
                copro_depth=args.copro_depth,
                copro_breadth=args.copro_breadth,
                copro_candidates=args.copro_candidates,
            )

            total_processed += 1

        print(f"   ✅ Done: {skill_dir_name}")

    print(f"\n{'='*70}")
    print(f"✅ All done!")
    print(f"   Processed: {total_processed} skill versions")
    print(f"   Skipped: {total_skipped} skill directories")
    print(f"   Output root: {args.output_root}/dataset={args.dataset_id}/")
    print(f"   Run stamp: {run_stamp}")

if __name__ == "__main__":
    main()
