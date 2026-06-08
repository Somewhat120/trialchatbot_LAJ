#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Agent scoring runner (Loop Mode) - vLLM backend
- Processes all skills from a single mixed JSONL file
- Automatically discovers all skill directories and versions
- Filters rows by criterion_id or human label key
- Output structure matches OpenAI agent runner
- Backend: vLLM OpenAI-compatible /v1/chat/completions
- Uses model_profiles.json + build_messages_from_profile()
- No enforced JSON mode; uses extract_score() + retries; stores raw output

Canonical vLLM agent entrypoint for all criteria when running no-RAG / control
agent skill scoring. This script does not retrieve evidence and always uses
empty context (NO_RAG_CONTROL).
"""

import argparse
import json
import os
import time
import hashlib
import re
import glob
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from openai import OpenAI

from dspy_engine.lm_vllm_chat import VLLMChatLM
from dspy_engine.run_dspy_vllm_noopt import call_dspy_vllm_noopt
from dspy_engine.utils_paths import resolve_latest_best_skill_path

# Retry when API returns empty or invalid score (transient failures)
MAX_API_RETRIES = 2
RETRY_DELAY_SECONDS = 3
DEFAULT_JSON_STOP_MARKER = "<END_JSON>"
STRICT_JSON_APPENDIX_TEMPLATE = (
    "\n\n### OUTPUT (STRICT JSON ONLY)\n"
    "Return exactly one minified JSON object with keys \"reasoning\" and \"final_score\".\n"
    "Then output the token {stop_marker} immediately after the closing brace.\n"
    "Do not output markdown, code fences, analysis, or any other text.\n"
    "Format: {{\"reasoning\":\"...\",\"final_score\":<integer>}}{stop_marker}\n"
)
COMPACT_RETRY_APPENDIX = (
    "\n\n### RETRY MODE\n"
    "Previous output was invalid. Keep this very short.\n"
    "Reasoning must be <= 2 sentences and <= 220 characters.\n"
    "Return only one minified JSON object and then {stop_marker}.\n"
    "Do not repeat text.\n"
)

# Thread-safe JSONL append if you later add concurrency
_write_lock = threading.Lock()

# ==========================================
# MAPPINGS (same as OpenAI script)
# ==========================================
SKILL_DIR_TO_CRITERION_ID = {
    "c1_critical_harm": "safety_c1_critical_harm",
    "c2_individualized_medical_advice": "safety_c2_individualized_medical_advice",
    "c3_inappropriate_persuasion": "safety_c3_inappropriate_persuasion",
    "c4_transparency": "reliability_c4_transparency",
    "c5_factual_accuracy": "reliability_c5_factual_accuracy",  # ADD THIS LINE
    "c6_usefulness": "communication_c6_usefulness",
    "c7_clarity": "communication_c7_clarity_succinctness",
}

SKILL_DIR_TO_HUMAN_KEY = {
    "c1_critical_harm": "Presence of critical harms",
    "c2_individualized_medical_advice": "Presence of individualized medical advice",
    "c3_inappropriate_persuasion": "Presence of inappropriate persuasion",
    "c4_transparency": "Level of source transparency",
    "c5_factual_accuracy": "Level of factual accuracy",  # ADD THIS LINE
    "c6_usefulness": "Level of usefulness",
    "c7_clarity": "Level of clarity and succinctness",
}

CRITERION_ID_TO_DOMAIN = {
    "safety_c1_critical_harm": "safety",
    "safety_c2_individualized_medical_advice": "safety",
    "safety_c3_inappropriate_persuasion": "safety",
    "reliability_c4_transparency": "reliability",
    "reliability_c5_factual_accuracy": "reliability",  # ADD THIS LINE
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

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

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
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def now_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def safe_str(x: Any) -> str:
    return "" if x is None else str(x)

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
    match = re.search(r"_v(\d+)$", base_name)
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
# MODEL PROFILES + MESSAGE BUILDING (copied from prompt runner)
# ==========================================

def load_model_profiles(path: str) -> dict:
    profiles = load_json(path)
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"Model profiles file must be a non-empty dict: {path}")
    return profiles

def render_user_text(template: str, context: str, question: str, answer: str) -> str:
    """
    Safe .format() rendering (brace-escape) copied from your prompt runner pattern.
    For skills, template will be the SKILL.md content.
    """
    t = safe_str(template)
    t = t.replace("{context}", "___CTX___").replace("{question}", "___Q___").replace("{answer}", "___A___")
    t = t.replace("{", "{{").replace("}", "}}")
    t = t.replace("___CTX___", "{context}").replace("___Q___", "{question}").replace("___A___", "{answer}")
    try:
        return t.format(context=context, question=question, answer=answer).strip()
    except Exception:
        try:
            return t.format(question=question, answer=answer).strip()
        except Exception:
            return f"{context}\n\nQUESTION:\n{question}\n\nANSWER:\n{answer}".strip()

def build_messages_from_profile(
    profile: dict,
    system_role: str,
    instruction_template: str,
    context: str,
    question: str,
    answer: str,
) -> List[Dict[str, Any]]:
    """
    Profile-driven message formatting. Mirrors your 02_LAJ_noRAG_loop.py logic,
    but adapted for (system_role, instruction_template) instead of PromptSpec.
    """
    user_text = render_user_text(instruction_template, context, question, answer)
    sys_text = system_role.strip() if system_role else ""
    merge_system = bool(profile.get("merge_system_into_user", False))

    if merge_system and sys_text:
        combined_user = f"{sys_text}\n\n{user_text}".strip()
        sys_text = ""
    else:
        combined_user = user_text

    content_style = profile.get("content_style", "string")
    if content_style == "openai_text_list":
        user_content = [{"type": "text", "text": combined_user}]
        system_content = [{"type": "text", "text": sys_text}] if sys_text else None
    else:
        user_content = combined_user
        system_content = sys_text if sys_text else None

    msg_style = profile.get("message_style", "system_user")
    messages: List[Dict[str, Any]] = []

    if msg_style == "system_user":
        if system_content:
            messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": user_content})
    elif msg_style == "user_only":
        if system_content and not merge_system:
            if content_style == "openai_text_list":
                user_content = [{"type": "text", "text": f"{sys_text}\n\n{combined_user}".strip()}]
            else:
                user_content = f"{sys_text}\n\n{combined_user}".strip()
        messages.append({"role": "user", "content": user_content})
    else:
        raise ValueError(f"Unknown message_style in profile: {msg_style}")

    return messages

# ==========================================
# SCORE EXTRACTION (copied from prompt runner)
# ==========================================

_FINAL_SCORE_RE = re.compile(
    r"final\s*_?\s*score[^0-9-]{0,20}(-?\d{1,2})",
    flags=re.IGNORECASE
)

def apply_strict_json_contract_to_skill(skill_text: str, stop_marker: str) -> str:
    appendix = STRICT_JSON_APPENDIX_TEMPLATE.format(stop_marker=stop_marker)
    return f"{skill_text.rstrip()}{appendix}"

def apply_compact_retry_contract_to_skill(skill_text: str, stop_marker: str) -> str:
    appendix = COMPACT_RETRY_APPENDIX.format(stop_marker=stop_marker)
    return f"{skill_text.rstrip()}{appendix}"

def extract_score(text: str, stop_marker: str) -> Tuple[int, Optional[str], str]:
    raw = safe_str(text)
    if not raw.strip():
        return (-1, "empty_response", "")

    if stop_marker and stop_marker in raw:
        raw = raw.split(stop_marker, 1)[0]

    s = strip_think_tags(raw).strip()
    if not s:
        return (-1, "missing_json", "")

    parse_error = None
    try:
        l = s.find("{")
        r = s.rfind("}")
        if l != -1 and r != -1 and r > l:
            sub = s[l:r + 1]
            obj = json.loads(sub)
            if isinstance(obj, dict) and "final_score" in obj:
                return (int(obj["final_score"]), None, s)
    except Exception as e:
        parse_error = str(e)

    m = _FINAL_SCORE_RE.search(s)
    if m:
        return (int(m.group(1)), None, s)

    return (-1, parse_error or "no_score_found", s)

# ------------------------------------------
# Helpers
# ------------------------------------------
def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks and trim whitespace."""
    if not isinstance(text, str):
        return text
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

# ==========================================
# vLLM CALL
# ==========================================

def call_vllm_chat(
    client: OpenAI,
    model: str,
    messages: List[Dict[str, Any]],
    gen_cfg: Dict[str, Any],
    *,
    stop_marker: str,
    no_json_mode: bool = False,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Returns dict with:
      - raw_output
      - ai_score
      - parse_error (optional)
      - error (optional)
      - latency_s
    """
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=gen_cfg.get("temperature", 0.0),
            top_p=gen_cfg.get("top_p", 1.0),
            max_tokens=gen_cfg.get("max_tokens", 512),
            stop=[stop_marker] if stop_marker else None,
            response_format=None if no_json_mode else {"type": "json_object"},
            extra_body=extra_body if extra_body else None,
        )
        latency = time.time() - t0
        raw = strip_think_tags((resp.choices[0].message.content or "").strip())
        finish_reason = getattr(resp.choices[0], "finish_reason", None)
        usage = getattr(resp, "usage", None)
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None

        score, parse_error, cleaned = extract_score(raw, stop_marker=stop_marker)
        out = {
            "raw_output": raw,
            "cleaned_output": cleaned,
            "ai_score": score,
            "latency_s": latency,
            "finish_reason": finish_reason,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        if score == -1:
            out["parse_error"] = parse_error or "failed_to_extract_score"
        return out

    except Exception as e:
        return {
            "error": str(e),
            "raw_output": "",
            "ai_score": -1,
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
    judge_model: str,
    model_profile_name: str,
    model_profile: dict,
    vllm_api_base: str,
    output_path: str,
    force: bool,
    gen_cfg: Dict[str, Any],
    args,
    dspy_lm,
    *,
    compiled_artifacts_root: Optional[str] = None,
    best_skill_cache: Optional[Dict[Tuple[str, str], Optional[str]]] = None,
    copro_depth: int = 1,
    copro_candidates: int = 3,
) -> None:

    skill_text = read_text(skill_path)
    skill_text_strict = apply_strict_json_contract_to_skill(skill_text, args.json_stop_marker)
    skill_text_compact = apply_compact_retry_contract_to_skill(skill_text_strict, args.json_stop_marker)
    skill_hash = md5(skill_text)
    skill_filename = os.path.basename(skill_path)

    # Resolve and cache best_skill.md content for dspy_opt_prompt (once per criterion+version)
    opt_prompt_skill_text = None
    if args.method == "dspy_opt_prompt" and compiled_artifacts_root and best_skill_cache is not None:
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
                backend="vllm",
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

    # Optional: keep system_role empty for comparability; rely on skill text.
    system_role = ""
    # Fixed no-rag note (optional). Keep short to save tokens.
    ctx = ""

    for row in rows:
        qa_id = get_qa_id(row)
        if qa_id is not None and (qa_id in seen_qa_ids) and (not force):
            continue

        trial = str(row.get("trial_num", "UNKNOWN"))
        
        # Normalize question_num (handle None/invalid values)
        # Check both question_num and question_num_raw_source (input files may use different field names)
        qnum_raw = row.get("question_num") or row.get("question_num_raw_source")
        try:
            qnum = int(float(qnum_raw)) if qnum_raw is not None else None
        except (ValueError, TypeError):
            qnum = None
        if qnum is None:
            qnum = "UNKNOWN"

        record_id = f"{trial}::{qnum}"
        # Include method in run_key to avoid cross-method collisions
        run_key = md5(f"{skill_hash}|{judge_model}|{model_profile_name}|{args.method}|{record_id}")

        # Support multiple field name variants for question/answer
        question = safe_str(row.get("question") or row.get("questions") or "")
        answer = safe_str(row.get("answer") or row.get("chatbot_answer") or row.get("response") or "")
        human_score = row.get(human_label_key)

        # Short-circuit empty Q/A to avoid wasted API calls
        if not question.strip() or not answer.strip():
            out_rec = {
                "status": "error",
                "record_id": record_id,
                "run_key": run_key,
                "trial_num": trial,
                "question_num": qnum,
                "question": question,
                "chatbot_answer": answer,
                "human_score": human_score,
                "criterion_id": criterion_id,
                "retrieved_context": "NO_RAG_CONTROL",
                "method": args.method,
                "skill_path": skill_path,
                "skill_filename": skill_filename,
                "skill_version": skill_version,
                "skill_hash": skill_hash,
                "judge_model": judge_model,
                "backend": "vllm",
                "model_profile": model_profile_name,
                "vllm_api_base": vllm_api_base,
                "generation_config": gen_cfg,
                "ai_reasoning": "",
                "ai_score": -1,
                "judge_output_raw": "",
                "parse_error": None,
                "error_msg": "empty_question_or_answer",
                "latency_s": None,
            }
            append_jsonl(output_path, out_rec)
            if qa_id is not None:
                seen_qa_ids.add(qa_id)
            continue

        # Call backend with retries
        result = None
        ai_reasoning = ""
        finish_reason = None
        completion_tokens = None
        prompt_tokens = None
        total_tokens = None
        retry_count = 0
        no_json_mode = bool(args.no_json_mode)
        json_mode_disabled_after_error = False
        prev_parse_error = None
        prev_finish_reason = None
        effective_max_tokens = args.max_tokens

        for attempt in range(max(0, int(args.max_retries)) + 1):
            retry_count = attempt
            if args.method == "agent":
                use_compact_retry = (
                    attempt > 0
                    and (
                        prev_finish_reason == "length"
                        or prev_parse_error in {"truncated_json", "missing_json", "malformed_json", "no_score_found"}
                    )
                )
                instruction_template = skill_text_compact if use_compact_retry else skill_text_strict
                effective_max_tokens = args.max_tokens
                if use_compact_retry:
                    effective_max_tokens = min(args.max_tokens, max(32, args.retry_max_tokens))

                extra_body: Dict[str, Any] = {}
                if "enable_thinking" in model_profile:
                    extra_body = {
                        "chat_template_kwargs": {
                            "enable_thinking": bool(model_profile["enable_thinking"])
                        }
                    }
                if not args.allow_thinking:
                    extra_body = {
                        **extra_body,
                        "chat_template_kwargs": {
                            **extra_body.get("chat_template_kwargs", {}),
                            "enable_thinking": False,
                        },
                    }

                messages = build_messages_from_profile(
                    profile=model_profile,
                    system_role=system_role,
                    instruction_template=instruction_template,
                    context=ctx,
                    question=question,
                    answer=answer,
                )
                run_cfg = {**gen_cfg, "max_tokens": effective_max_tokens}
                result = call_vllm_chat(
                    client=client,
                    model=judge_model,
                    messages=messages,
                    gen_cfg=run_cfg,
                    stop_marker=args.json_stop_marker,
                    no_json_mode=no_json_mode,
                    extra_body=extra_body,
                )
                ai_reasoning = result.get("cleaned_output") or strip_think_tags(result.get("raw_output", ""))
                finish_reason = result.get("finish_reason")
                completion_tokens = result.get("completion_tokens")
                prompt_tokens = result.get("prompt_tokens")
                total_tokens = result.get("total_tokens")
                prev_parse_error = result.get("parse_error")
                prev_finish_reason = finish_reason
                err_txt = safe_str(result.get("error"))
                if (err_txt and not no_json_mode and ("response_format" in err_txt.lower() or "json" in err_txt.lower())):
                    no_json_mode = True
                    json_mode_disabled_after_error = True
            elif args.method == "dspy_noopt":
                result = call_dspy_vllm_noopt(
                    lm=dspy_lm,
                    skill_text=skill_text,
                    question=question,
                    answer=answer,
                    criterion_id=criterion_id,
                )
                ai_reasoning = strip_think_tags(result.get("ai_reasoning", ""))
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
                    result = call_dspy_vllm_noopt(
                        lm=dspy_lm,
                        skill_text=opt_prompt_skill_text,
                        question=question,
                        answer=answer,
                        criterion_id=criterion_id,
                    )
                    ai_reasoning = strip_think_tags(result.get("ai_reasoning", ""))
            else:
                result = {
                    "error": f"Unknown method: {args.method}",
                    "ai_score": -1,
                    "raw_output": "",
                    "ai_reasoning": "",
                    "parse_error": None,
                    "latency_s": None,
                }

            need_retry = (
                (result.get("raw_output") or "") == "" or (result.get("ai_score", -1) == -1)
            ) and attempt < max(0, int(args.max_retries))

            if not need_retry:
                break
            time.sleep(max(0.0, float(args.retry_delay_s)))

        # Status rule: error if exception OR score extraction failed
        status = "ok"
        if (result.get("error") is not None) or (result.get("ai_score", -1) == -1):
            status = "error"

        # Ensure parse_error is always set when score=-1 (for consistency)
        parse_error = result.get("parse_error")
        if result.get("ai_score", -1) == -1 and not parse_error:
            parse_error = "no_score_found"

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
            "method": args.method,

            "skill_path": skill_path,
            "skill_filename": skill_filename,
            "skill_version": skill_version,
            "skill_hash": skill_hash,

            "judge_model": judge_model,
            "backend": "vllm",
            "model_profile": model_profile_name,
            "vllm_api_base": vllm_api_base,

            "generation_config": gen_cfg,

            # Keep raw output always
            "ai_reasoning": ai_reasoning,
            "ai_score": result.get("ai_score", -1),
            "judge_output_raw": result.get("raw_output", ""),
            "parse_error": parse_error,
            "error_msg": result.get("error"),
            "finish_reason": finish_reason,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "retry_count": retry_count,
            "json_mode_disabled_after_error": json_mode_disabled_after_error,
            "effective_max_tokens": effective_max_tokens,
            "latency_s": result.get("latency_s"),
        }

        append_jsonl(output_path, out_rec)
        if qa_id is not None:
            seen_qa_ids.add(qa_id)

# ==========================================
# MAIN
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Agent scoring runner (Loop Mode) - vLLM backend"
    )
    parser.add_argument("--input_jsonl", required=True, help="Mixed JSONL file with all criteria")
    parser.add_argument("--skills_root", type=str, default="skills", help="Root directory containing skill domains")
    parser.add_argument("--skills_dir", type=str, default=None, help="Optional single skill dir relative to skills_root (e.g., safety/c1_critical_harm)")
    parser.add_argument("--dataset_id", type=str, required=True, help="Dataset ID for output path")
    parser.add_argument("--judge_model", type=str, required=True, help="Served model name on vLLM (--served-model-name)")
    parser.add_argument("--vllm_api_base", type=str, default="http://localhost:8000/v1", help="vLLM base URL (OpenAI-compatible)")
    parser.add_argument("--model_profiles_json", type=str, default="model_configs/model_profiles.json", help="Model profiles JSON")
    parser.add_argument("--model_profile", type=str, required=True, help="Profile key in model_profiles.json")
    parser.add_argument("--output_root", type=str, default="LAJ_score_outputs", help="Output root directory")
    parser.add_argument("--force", action="store_true", help="Ignore resume/dedup and re-run")
    parser.add_argument("--test_rows", type=int, default=None, help="Limit processing to first N rows (for testing)")
    parser.add_argument("--excluded_skills", nargs="*", default=[], help="Skill filenames to exclude (e.g., SKILL_v0.md)")
    parser.add_argument("--max_tokens", type=int, default=512, help="Max output tokens (vLLM)")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature (vLLM)")
    parser.add_argument("--top_p", type=float, default=1.0, help="Top-p (vLLM)")
    parser.add_argument("--max_retries", type=int, default=2, help="Retries for parse/empty/backend failures")
    parser.add_argument("--retry_delay_s", type=float, default=1.5, help="Delay between retries")
    parser.add_argument("--retry_max_tokens", type=int, default=320, help="Token cap used on compact retries after parse/length failures")
    parser.add_argument("--json_stop_marker", type=str, default=DEFAULT_JSON_STOP_MARKER, help="Stop marker appended after JSON output")
    parser.add_argument("--no_json_mode", action="store_true", help="Disable response_format=json_object")
    parser.add_argument("--allow_thinking", action="store_true", help="Allow reasoning/thinking output")
    parser.add_argument("--method", type=str, default="agent",
                    choices=["agent", "dspy_noopt", "dspy_opt_prompt"],
                    help="Scoring method: agent, dspy_noopt, or dspy_opt_prompt (DSPy + COPRO rewritten SKILL)")
    parser.add_argument("--compiled_artifacts_root", type=str, default="dspy_engine/compiled_artifacts",
                    help="Root of compiled artifacts (for method=dspy_opt_prompt)")
    parser.add_argument("--copro_depth", type=int, default=1, help="COPRO depth used at compile (for resolving best_skill)")
    parser.add_argument("--copro_candidates", type=int, default=3, help="COPRO candidates used at compile (for resolving best_skill)")
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
    if args.skills_dir:
        want = os.path.normpath(args.skills_dir).replace("\\", "/").strip("/")
        filtered = []
        for skill_dir_path, skill_dir_name, domain in skill_dirs:
            rel = os.path.relpath(skill_dir_path, args.skills_root).replace("\\", "/")
            if rel == want:
                filtered.append((skill_dir_path, skill_dir_name, domain))
        skill_dirs = filtered
    print(f"   Found {len(skill_dirs)} skill directories")
    if not skill_dirs:
        print("⚠️  No skill directories found!")
        return

    # Load model profile
    profiles = load_model_profiles(args.model_profiles_json)
    if args.model_profile not in profiles:
        raise RuntimeError(f"Unknown model_profile={args.model_profile}. Available: {list(profiles.keys())}")
    model_profile = profiles[args.model_profile]

    # vLLM client + connectivity test
    print(f"\n🤖 Connecting to vLLM: {args.vllm_api_base}")
    print(f"   Served model: {args.judge_model}")
    client = OpenAI(base_url=args.vllm_api_base, api_key="EMPTY")
    try:
        models = client.models.list()
        served_models = {m.id for m in models.data}
        print(f"✅ Connected. Available models: {sorted(served_models)}")
        
        # Validate that requested model is actually served
        if args.judge_model not in served_models:
            raise RuntimeError(
                f"judge_model='{args.judge_model}' not found in served models. "
                f"Available: {sorted(served_models)}"
            )
    except Exception as e:
        raise RuntimeError(f"Failed to connect to vLLM server: {e}")

    # Instantiate DSPy LM if needed (used for dspy_noopt and dspy_opt_prompt)
    dspy_lm = None
    if args.method in ("dspy_noopt", "dspy_opt_prompt"):
        dspy_lm = VLLMChatLM(
            client=client,
            model=args.judge_model,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            message_style=model_profile.get("message_style", "user_only"),
            content_style=model_profile.get("content_style", "openai_text_list"),
            merge_system_into_user=bool(model_profile.get("merge_system_into_user", True)),
            system_role="",  # keep empty for parity with your agent runner
        )

    run_stamp = args.run_stamp if args.run_stamp else now_run_stamp()
    gen_cfg = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
    }

    overall_start_time = time.time()
    total_processed = 0
    total_skipped = 0
    best_skill_cache: Dict[Tuple[str, str], Optional[str]] = {} if args.method == "dspy_opt_prompt" else {}

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

        skill_dir_start_time = time.time()
        print(f"\n{'='*70}")
        print(f"📝 Processing: {skill_dir_name}")
        print(f"   Criterion: {criterion_id}")
        print(f"   Domain: {domain}")
        print(f"   Matching rows: {len(rows_for_skill)}")

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
            "backend=vllm",
            f"model={args.judge_model}",
            "evidence=no_rag_control",
            f"variation={args.method}",
            f"run={run_stamp}",
        )
        os.makedirs(out_dir, exist_ok=True)

        for skill_file in skill_files:
            skill_filename = os.path.basename(skill_file)
            skill_version = extract_version_from_filename(skill_filename)

            output_filename = f"{skill_dir_name}_{skill_version}.jsonl" if skill_version else f"{skill_dir_name}.jsonl"
            output_path = os.path.join(out_dir, output_filename)

            skill_version_start_time = time.time()
            print(f"   → {skill_filename} (version: {skill_version or 'default'}) -> {output_filename}")

            process_skill_version_with_rows(
                skill_path=skill_file,
                skill_version=skill_version,
                skill_dir_name=skill_dir_name,
                criterion_id=criterion_id,
                human_label_key=human_key,
                rows=rows_for_skill,
                client=client,
                judge_model=args.judge_model,
                model_profile_name=args.model_profile,
                model_profile=model_profile,
                vllm_api_base=args.vllm_api_base,
                output_path=output_path,
                force=args.force,
                gen_cfg=gen_cfg,
                args=args,
                dspy_lm=dspy_lm,
                compiled_artifacts_root=args.compiled_artifacts_root if args.method == "dspy_opt_prompt" else None,
                best_skill_cache=best_skill_cache if args.method == "dspy_opt_prompt" else None,
                copro_depth=args.copro_depth,
                copro_candidates=args.copro_candidates,
            )

            total_processed += 1
            skill_version_elapsed = time.time() - skill_version_start_time
            print(f"      Time: {skill_version_elapsed:.1f}s ({skill_version_elapsed/60:.1f} min)")

        skill_dir_elapsed = time.time() - skill_dir_start_time
        print(f"   ✅ Done: {skill_dir_name} (total: {skill_dir_elapsed:.1f}s / {skill_dir_elapsed/60:.1f} min)")

    overall_elapsed = time.time() - overall_start_time
    print(f"\n{'='*70}")
    print("✅ All done!")
    print(f"   Processed: {total_processed} skill versions")
    print(f"   Skipped: {total_skipped} skill directories")
    print(f"   Total time: {overall_elapsed:.1f}s ({overall_elapsed/60:.1f} min)")
    print(f"   Output root: {args.output_root}/dataset={args.dataset_id}/")
    print(f"   Run stamp: {run_stamp}")

if __name__ == "__main__":
    main()
