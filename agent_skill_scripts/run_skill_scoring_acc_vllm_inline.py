#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
run_skill_scoring_acc_vllm_inline.py
------------------------------------
Inline C5-only agent runner (vLLM backend) with on-the-fly retrieval.

This script parallels agent_skill_scripts/run_skill_scoring_acc_vllm.py but consumes the raw
eval JSONL, retrieves evidence inline using prompt_evaluation_scripts/
01_retrieval_chunk.py, and supports both:
- method=agent
- method=dspy_opt_prompt
"""

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from dspy_engine.lm_vllm_chat import VLLMChatLM
from dspy_engine.run_dspy_vllm_noopt import call_dspy_vllm_noopt
from dspy_engine.utils_paths import resolve_latest_best_skill_path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("MKL_NUM_THREADS", "2"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", os.getenv("OPENBLAS_NUM_THREADS", "2"))
os.environ.setdefault("NUMEXPR_NUM_THREADS", os.getenv("NUMEXPR_NUM_THREADS", "2"))

C5_SKILL_DIR_NAME = "c5_factual_accuracy"
C5_CRITERION_ID = "reliability_c5_factual_accuracy"
C5_DOMAIN = "reliability"
C5_HUMAN_KEY = "Level of factual accuracy"
DEFAULT_EVIDENCE_TAG = "inline_icf_chunks_k5"
DEFAULT_JSON_STOP_MARKER = "<END_JSON>"
MAX_API_RETRIES = 2
RETRY_DELAY_SECONDS = 3.0

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
THOUGHT_RE = re.compile(
    r"<(?:unused94|think|start_of_thought)>\s*(.*?)\s*<(?:unused95|/think|end_of_thought)>",
    flags=re.DOTALL | re.IGNORECASE,
)
THOUGHT_UNCLOSED_RE = re.compile(
    r"<(?:unused94|think|start_of_thought)>\s*(.*)\Z",
    flags=re.DOTALL | re.IGNORECASE,
)
_EVIDENCE_BLOCK_RE = re.compile(
    r"^=== \[EVIDENCE \d+ .*?(?=^=== \[EVIDENCE \d+ |\Z)",
    flags=re.MULTILINE | re.DOTALL,
)
write_lock = threading.Lock()


def now_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with write_lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(obj, ensure_ascii=False) + "\n")


def discover_skills(skills_dir: str, excluded_list: Optional[List[str]] = None) -> List[str]:
    excluded_list = excluded_list or []
    files = glob.glob(os.path.join(skills_dir, "SKILL*.md"))
    files = [path for path in files if os.path.basename(path) not in excluded_list]
    files.sort()
    return files


def extract_version_from_filename(filename: str) -> Optional[str]:
    match = re.search(r"_v(\d+)$", os.path.splitext(filename)[0])
    return f"v{match.group(1)}" if match else None


def get_qa_id(row: Dict[str, Any]) -> Optional[int]:
    qa_id = row.get("qa_id")
    if qa_id is None:
        return None
    try:
        return int(float(qa_id))
    except (TypeError, ValueError):
        return None


def build_record_id(row: Dict[str, Any]) -> str:
    qa_id = get_qa_id(row)
    if qa_id is not None:
        return str(qa_id)
    trial = safe_str(row.get("trial_num", "UNKNOWN")).strip() or "UNKNOWN"
    qn = row.get("question_num") or row.get("question_num_raw_source") or "UNKNOWN"
    return f"{trial}::{qn}"


def row_is_c5(row: Dict[str, Any]) -> bool:
    cid = row.get("criterion_id")
    if cid and str(cid) == C5_CRITERION_ID:
        return True
    if C5_HUMAN_KEY in row:
        return True
    if row.get("human_score") is not None:
        return True
    return False


def normalize_raw_row(row: Dict[str, Any]) -> Dict[str, Any]:
    rec = dict(row)
    if not safe_str(rec.get("answer")).strip():
        rec["answer"] = rec.get("chatbot_answer", "")
    if not safe_str(rec.get("chatbot_answer")).strip():
        rec["chatbot_answer"] = rec.get("answer", "")
    if "human_score" not in rec and rec.get(C5_HUMAN_KEY) is not None:
        rec["human_score"] = rec.get(C5_HUMAN_KEY)
    if C5_HUMAN_KEY not in rec and rec.get("human_score") is not None:
        rec[C5_HUMAN_KEY] = rec.get("human_score")
    return rec


def load_model_profiles(path: str) -> Dict[str, Any]:
    profiles = load_json(path)
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"Model profiles file must be a non-empty dict: {path}")
    return profiles


def render_user_text(template: str, context: str, question: str, answer: str) -> str:
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
    profile: Dict[str, Any],
    system_text: str,
    user_text: str,
) -> List[Dict[str, Any]]:
    merge_system = bool(profile.get("merge_system_into_user", False))
    if merge_system and system_text:
        combined_user = f"{system_text}\n\n{user_text}".strip()
        system_text = ""
    else:
        combined_user = user_text

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


def apply_strict_json_contract(user_prompt: str, stop_marker: str) -> str:
    return f"{user_prompt.rstrip()}{STRICT_JSON_APPENDIX_TEMPLATE.format(stop_marker=stop_marker)}"


def apply_compact_retry_contract(user_prompt: str, stop_marker: str) -> str:
    return f"{user_prompt.rstrip()}{COMPACT_RETRY_APPENDIX.format(stop_marker=stop_marker)}"


def strip_think_tags(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def parse_reasoning_and_json(raw_text: str) -> Tuple[str, str]:
    match = THOUGHT_RE.search(raw_text)
    if match:
        reasoning = match.group(1).strip()
        remainder = (raw_text[:match.start()] + raw_text[match.end():]).strip()
        return reasoning, remainder
    match2 = THOUGHT_UNCLOSED_RE.search(raw_text)
    if match2:
        return match2.group(1).strip(), raw_text[:match2.start()].strip()
    return "", raw_text.strip()


def _find_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(text, str):
        return None, None
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).replace("```", "").strip()
    if not cleaned or "{" not in cleaned or "}" not in cleaned:
        return None, None
    left_positions = [i for i, ch in enumerate(cleaned) if ch == "{"]
    right_positions = [i for i, ch in enumerate(cleaned) if ch == "}"]
    candidates: List[str] = []
    for right in reversed(right_positions):
        for left in reversed([pos for pos in left_positions if pos < right]):
            candidates.append(cleaned[left:right + 1])
            if len(candidates) >= 20:
                break
        if len(candidates) >= 20:
            break
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj, cand
        except Exception:
            continue
    return None, None


def extract_score(text: str, stop_marker: Optional[str] = None) -> Tuple[int, Optional[str], str]:
    raw = strip_think_tags(text or "")
    _, clean_json = parse_reasoning_and_json(raw)
    normalized = (clean_json or raw).replace("{{", "{").replace("}}", "}")
    if stop_marker:
        normalized = normalized.replace(stop_marker, "").strip()
    if not normalized:
        return -1, "missing_json", ""

    obj, extracted = _find_json_object(normalized)
    if obj is not None:
        for key in ("final_score", "score"):
            if key in obj:
                try:
                    return int(obj[key]), None, extracted or normalized
                except Exception:
                    return -1, "malformed_json", extracted or normalized
        return -1, "no_score_found", extracted or normalized

    match = re.search(
        r"final\s*_?\s*score[^0-9-]{0,20}(-?\d{1,2})",
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        return int(match.group(1)), None, normalized
    return -1, "missing_json", normalized


def trim_evidence_blocks(text: str, max_blocks: int) -> str:
    if not isinstance(text, str) or max_blocks <= 0:
        return text
    blocks = _EVIDENCE_BLOCK_RE.findall(text)
    if not blocks:
        return text
    return "\n\n".join(blocks[:max_blocks]).strip()


def truncate_text(text: str, max_chars: int) -> str:
    if not text or max_chars <= 0:
        return text
    return text if len(text) <= max_chars else text[:max_chars]


def is_valid_completed_record(rec: Dict[str, Any]) -> bool:
    if rec.get("status") != "ok":
        return False
    if rec.get("parse_error"):
        return False
    try:
        score = int(float(rec.get("ai_score")))
    except (TypeError, ValueError):
        return False
    return score != -1


def read_existing_record_ids(output_path: str) -> set:
    seen = set()
    if not os.path.exists(output_path):
        return seen
    with open(output_path, "r", encoding="utf-8") as handle:
        for line in handle:
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
            if rid is not None:
                seen.add(str(rid))
    return seen


def load_stage1_module():
    stage1_path = Path(__file__).resolve().parent.parent / "prompt_evaluation_scripts" / "01_retrieval_chunk.py"
    spec = importlib.util.spec_from_file_location("laj_stage1_retrieval", stage1_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load stage1 module from {stage1_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_valid_trial_ids(db_folder: str) -> set:
    trial_index_path = os.path.join(db_folder, "available_trials.json")
    if not os.path.exists(trial_index_path):
        return set()
    with open(trial_index_path, "r", encoding="utf-8") as handle:
        return set(json.load(handle))


def init_retrieval_runtime(args, stage1):
    print(f"⏳ Loading inline retrieval embeddings on {args.embedding_device}: {args.embedding_model}")
    try:
        embedding_fn = stage1.HuggingFaceEmbeddings(
            model_name=args.embedding_model,
            model_kwargs={
                "trust_remote_code": True,
                "device": args.embedding_device,
            },
        )
    except Exception as exc:
        msg = str(exc)
        if ("additional_chat_templates" in msg) or ("RemoteEntryNotFoundError" in msg) or ("404" in msg):
            embedding_fn = stage1.HuggingFaceEmbeddings(
                model_name=args.embedding_model,
                model_kwargs={
                    "trust_remote_code": True,
                    "device": args.embedding_device,
                    "local_files_only": True,
                },
            )
        else:
            raise

    db = stage1.Chroma(persist_directory=args.db_folder, embedding_function=embedding_fn)

    if args.reranker_type == "none":
        reranker = stage1.NullReranker()
        reranker_name = "none"
    elif args.reranker_type in ["bge", "mxbai"]:
        model_name = args.bge_reranker_model if args.reranker_type == "bge" else args.mxbai_reranker_model
        reranker = stage1.CrossEncoderReranker(model_name=model_name, device=args.reranker_device)
        reranker_name = model_name
    else:
        reranker = stage1.Qwen3Reranker4B(model_name=args.qwen_reranker_model, device=args.reranker_device)
        reranker_name = args.qwen_reranker_model

    retrieval_settings = {
        "input_jsonl": args.input_jsonl,
        "db_folder": args.db_folder,
        "retrieval_mode": args.retrieval_mode,
        "dense_k": args.dense_k,
        "bm25_k": args.bm25_k,
        "rrf_k": args.rrf_k,
        "final_k": args.final_k,
        "embedding_model": args.embedding_model,
        "embedding_device": args.embedding_device,
        "reranker_type": args.reranker_type,
        "reranker_model": reranker_name,
        "reranker_device": args.reranker_device,
        "reranker_batch_size": args.reranker_batch_size,
        "max_doc_chars_rerank": args.max_doc_chars_rerank,
        "retrieval_inflight": args.retrieval_inflight,
        "max_context_chars": args.max_context_chars,
        "trim_evidence_to_final_k": bool(args.trim_evidence_to_final_k),
    }
    return {
        "db": db,
        "reranker": reranker,
        "bm25_cache": {},
        "valid_trial_ids": build_valid_trial_ids(args.db_folder),
        "retrieval_settings": retrieval_settings,
    }


def retrieve_inline_context(
    row: Dict[str, Any],
    args,
    stage1,
    retrieval_runtime,
    retrieval_sema: threading.Semaphore,
) -> Tuple[str, List[dict], str, str, float]:
    retrieval_input = normalize_raw_row(row)
    t0 = time.time()
    with retrieval_sema:
        retrieval_obj = stage1.process_one(
            retrieval_input,
            retrieval_runtime["db"],
            retrieval_runtime["reranker"],
            retrieval_runtime["bm25_cache"],
            args.retrieval_mode,
            args.dense_k,
            args.bm25_k,
            args.rrf_k,
            args.final_k,
            args.reranker_type,
            args.reranker_batch_size,
            args.max_doc_chars_rerank,
            retrieval_runtime["valid_trial_ids"],
        )
    dt = time.time() - t0

    raw_context = safe_str(retrieval_obj.get("retrieved_context", "")).strip()
    if args.trim_evidence_to_final_k:
        raw_context = trim_evidence_blocks(raw_context, args.final_k)
    if args.max_context_chars > 0:
        raw_context = truncate_text(raw_context, args.max_context_chars).strip()

    retrieval_status = safe_str(retrieval_obj.get("status", "unknown")) or "unknown"
    context_missing_reason = "context_present" if raw_context else retrieval_status
    retrieval_meta = retrieval_obj.get("retrieval_meta", [])
    retrieval_meta_used = retrieval_meta if isinstance(retrieval_meta, list) else []
    return raw_context, retrieval_meta_used, retrieval_status, context_missing_reason, dt


def call_vllm_chat_strict(
    client: OpenAI,
    model: str,
    model_profile: Dict[str, Any],
    system_role: str,
    user_prompt: str,
    args,
    vllm_sema: threading.Semaphore,
) -> Dict[str, Any]:
    strict_user_prompt = apply_strict_json_contract(user_prompt, args.json_stop_marker)
    compact_retry_prompt = apply_compact_retry_contract(user_prompt, args.json_stop_marker)
    messages = build_messages_from_profile(model_profile, system_role, strict_user_prompt)
    compact_messages = build_messages_from_profile(model_profile, system_role, compact_retry_prompt)

    extra_body: Dict[str, Any] = {}
    chat_template_kwargs: Dict[str, Any] = {}
    if "enable_thinking" in model_profile:
        chat_template_kwargs["enable_thinking"] = bool(model_profile["enable_thinking"])
    if "thinking_mode" in model_profile:
        chat_template_kwargs["thinking_mode"] = model_profile["thinking_mode"]
    if not args.allow_thinking:
        chat_template_kwargs["enable_thinking"] = False
        if "thinking_mode" in model_profile:
            chat_template_kwargs["thinking_mode"] = "off"
    if chat_template_kwargs:
        extra_body["chat_template_kwargs"] = chat_template_kwargs

    t0 = time.time()
    text = ""
    hidden_thoughts = ""
    clean_json = ""
    score = -1
    status = "error"
    err = None
    parse_error = None
    finish_reason = None
    no_json_mode = bool(args.no_json_mode)
    prev_parse_error = None
    prev_finish_reason = None

    for attempt in range(MAX_API_RETRIES + 1):
        use_compact_retry = (
            attempt > 0 and (
                prev_finish_reason == "length"
                or prev_parse_error in {"truncated_json", "missing_json", "malformed_json", "no_score_found"}
            )
        )
        req_messages = compact_messages if use_compact_retry else messages
        effective_max_tokens = args.max_tokens if not use_compact_retry else min(args.max_tokens, args.retry_max_tokens)
        try:
            req_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": req_messages,
                "temperature": args.temperature,
                "top_p": args.top_p,
                "max_tokens": effective_max_tokens,
            }
            if extra_body:
                req_kwargs["extra_body"] = extra_body
            if args.json_stop_marker:
                req_kwargs["stop"] = [args.json_stop_marker]
            if not no_json_mode:
                req_kwargs["response_format"] = {"type": "json_object"}
            with vllm_sema:
                resp = client.chat.completions.create(**req_kwargs)
            text = strip_think_tags(safe_str(resp.choices[0].message.content))
            finish_reason = safe_str(getattr(resp.choices[0], "finish_reason", None)) or None
            prev_finish_reason = finish_reason
            hidden_thoughts, _ = parse_reasoning_and_json(text)
            score, parse_error, clean_json = extract_score(text, args.json_stop_marker)
            prev_parse_error = parse_error
            status = "ok"
            err = None
            if score != -1:
                break
            if attempt < MAX_API_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
        except Exception as exc:
            err = str(exc)
            parse_error = "backend_error"
            if (not no_json_mode) and ("response_format" in err.lower() or "json" in err.lower()):
                no_json_mode = True
            if attempt < MAX_API_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            break

    latency = time.time() - t0
    reasoning_from_json = ""
    if not hidden_thoughts and clean_json:
        try:
            left, right = clean_json.find("{"), clean_json.rfind("}")
            if left != -1 and right != -1 and right > left:
                obj = json.loads(clean_json[left:right + 1])
                reasoning_from_json = safe_str(obj.get("reasoning", ""))
        except Exception:
            reasoning_from_json = ""
    ai_reasoning = hidden_thoughts or reasoning_from_json or clean_json or text
    return {
        "status": status,
        "ai_score": score,
        "ai_reasoning": ai_reasoning,
        "raw_output": clean_json or text,
        "parse_error": parse_error if score == -1 and status == "ok" else None,
        "error": err,
        "latency_s": latency,
        "finish_reason": finish_reason,
    }


def build_output_record(
    *,
    row: Dict[str, Any],
    record_id: str,
    qa_id: Optional[int],
    skill_file: str,
    skill_filename: str,
    skill_version: Optional[str],
    skill_hash: str,
    args,
    ctx_used: str,
    retrieval_meta_used: List[dict],
    retrieval_status: str,
    context_missing_reason: str,
    retrieval_latency_s: float,
    judge_result: Dict[str, Any],
    judge_latency_s: Optional[float],
    total_latency_s: float,
    retrieval_settings: Dict[str, Any],
) -> Dict[str, Any]:
    question = safe_str(row.get("question", "")).strip()
    answer = safe_str(row.get("chatbot_answer") or row.get("answer", "")).strip()
    human_score = row.get(C5_HUMAN_KEY)
    if human_score is None:
        human_score = row.get("human_score")

    status = "ok"
    if judge_result.get("error") is not None or judge_result.get("ai_score", -1) == -1:
        status = "error"

    parse_error = judge_result.get("parse_error")
    if judge_result.get("ai_score", -1) == -1 and not parse_error and not judge_result.get("error"):
        parse_error = "no_score_found"

    return {
        "status": status,
        "record_id": record_id,
        "run_key": md5_text(f"{skill_hash}|{args.judge_model}|{args.model_profile}|{args.method}|{record_id}"),
        "qa_id": qa_id,
        "dataset_id": args.dataset_id,
        "trial_num": safe_str(row.get("trial_num", "UNKNOWN")).strip(),
        "question_num": row.get("question_num") or row.get("question_num_raw_source"),
        "question": question,
        "chatbot_answer": answer,
        "human_score": human_score,
        "criterion_id": C5_CRITERION_ID,
        "retrieved_context": "ICF_CHUNKS_RAG" if ctx_used else "NO_RETRIEVED_CONTEXT",
        "retrieved_context_used": ctx_used,
        "retrieval_meta_used": retrieval_meta_used,
        "retrieval_settings": retrieval_settings,
        "retrieval_status": retrieval_status,
        "context_missing_reason": context_missing_reason,
        "method": args.method,
        "skill_path": skill_file,
        "skill_filename": skill_filename,
        "skill_version": skill_version,
        "skill_hash": skill_hash,
        "judge_model": args.judge_model,
        "backend": "vllm",
        "model_profile": args.model_profile,
        "vllm_api_base": args.vllm_api_base,
        "evidence_tag": args.evidence_tag,
        "generation_config": {
            "temperature": args.temperature,
            "top_p": args.top_p,
            "max_tokens": args.max_tokens,
            "allow_thinking": bool(args.allow_thinking),
            "final_k": args.final_k,
        },
        "ai_reasoning": judge_result.get("ai_reasoning", ""),
        "ai_score": judge_result.get("ai_score", -1),
        "judge_output_raw": judge_result.get("raw_output", ""),
        "parse_error": parse_error,
        "error_msg": judge_result.get("error"),
        "finish_reason": judge_result.get("finish_reason"),
        "latency_s": round(judge_latency_s, 4) if judge_latency_s is not None else None,
        "judge_latency_s": round(judge_latency_s, 4) if judge_latency_s is not None else None,
        "retrieval_latency_s": round(retrieval_latency_s, 4),
        "total_latency_s": round(total_latency_s, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="C5-only inline agent runner (vLLM + retrieval)")
    parser.add_argument("--input_jsonl", required=True, help="Raw eval JSONL prior to stage1 retrieval")
    parser.add_argument("--dataset_id", required=True)
    parser.add_argument("--skills_dir", type=str, default="judge_skills/reliability/c5_factual_accuracy")
    parser.add_argument("--judge_model", type=str, required=True)
    parser.add_argument("--vllm_api_base", type=str, default=os.getenv("VLLM_API_BASE", "http://localhost:8000/v1"))
    parser.add_argument("--model_profiles_json", type=str, default="model_configs/model_profiles.json")
    parser.add_argument("--model_profile", type=str, required=True)
    parser.add_argument("--output_root", type=str, default="LAJ_score_outputs")
    parser.add_argument("--evidence_tag", type=str, default=DEFAULT_EVIDENCE_TAG)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--test_rows", type=int, default=None)
    parser.add_argument("--excluded_skills", nargs="*", default=[])
    parser.add_argument("--max_tokens", type=int, default=768)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top_p", type=float, default=1.0)
    parser.add_argument("--run_stamp", type=str, default=None)
    parser.add_argument("--max_context_chars", type=int, default=0)
    parser.add_argument("--trim_evidence_to_final_k", action="store_true")
    parser.add_argument("--allow_thinking", action="store_true")
    parser.add_argument("--json_stop_marker", type=str, default=DEFAULT_JSON_STOP_MARKER)
    parser.add_argument("--no_json_mode", action="store_true")
    parser.add_argument("--retry_max_tokens", type=int, default=320)
    parser.add_argument("--retrieval_inflight", type=int, default=1)
    parser.add_argument("--vllm_inflight", type=int, default=4)
    parser.add_argument(
        "--method",
        type=str,
        default="agent",
        choices=["agent", "dspy_opt_prompt"],
    )
    parser.add_argument("--compiled_artifacts_root", type=str, default="dspy_engine/compiled_artifacts")
    parser.add_argument("--copro_depth", type=int, default=1)
    parser.add_argument("--copro_candidates", type=int, default=3)

    parser.add_argument("--db_folder", type=str, default=os.getenv("DB_FOLDER", "data_preparation/chroma_db_multiscale"))
    parser.add_argument("--retrieval_mode", choices=["dense_only", "bm25_only", "hybrid_rrf"], default=os.getenv("RETRIEVAL_MODE", "hybrid_rrf"))
    parser.add_argument("--dense_k", type=int, default=int(os.getenv("DENSE_K", "10")))
    parser.add_argument("--bm25_k", type=int, default=int(os.getenv("BM25_K", "10")))
    parser.add_argument("--rrf_k", type=int, default=int(os.getenv("RRF_K", "60")))
    parser.add_argument("--final_k", type=int, default=int(os.getenv("FINAL_K", "5")))
    parser.add_argument("--embedding_model", type=str, default=os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"))
    parser.add_argument("--embedding_device", type=str, default=os.getenv("EMBEDDING_DEVICE", "cuda"))
    parser.add_argument("--reranker_type", choices=["none", "qwen", "bge", "mxbai"], default=os.getenv("RERANKER_TYPE", "qwen"))
    parser.add_argument("--reranker_device", type=str, default=os.getenv("RERANKER_DEVICE", "cuda"))
    parser.add_argument("--reranker_batch_size", type=int, default=int(os.getenv("RERANKER_BATCH_SIZE", "3")))
    parser.add_argument("--max_doc_chars_rerank", type=int, default=int(os.getenv("MAX_DOC_CHARS_RERANK", "4000")))
    parser.add_argument("--qwen_reranker_model", type=str, default=os.getenv("QWEN_RERANKER_MODEL", "Qwen/Qwen3-Reranker-4B"))
    parser.add_argument("--bge_reranker_model", type=str, default=os.getenv("BGE_RERANKER_MODEL", "BAAI/bge-reranker-large"))
    parser.add_argument("--mxbai_reranker_model", type=str, default=os.getenv("MXBAI_RERANKER_MODEL", "mixedbread-ai/mxbai-rerank-large-v2"))
    args = parser.parse_args()

    profiles = load_model_profiles(args.model_profiles_json)
    if args.model_profile not in profiles:
        raise RuntimeError(f"Unknown model_profile={args.model_profile}. Available: {list(profiles.keys())}")
    model_profile = profiles[args.model_profile]

    all_rows = load_jsonl(args.input_jsonl)
    rows = [normalize_raw_row(row) for row in all_rows if row_is_c5(row)]
    if args.test_rows and args.test_rows > 0:
        rows = rows[: args.test_rows]

    print(f"📂 Input JSONL: {args.input_jsonl}")
    print(f"   Total rows: {len(all_rows)} | C5 rows: {len(rows)}")
    if not rows:
        print("⚠️ No C5 rows found. Exiting.")
        return

    client = OpenAI(base_url=args.vllm_api_base, api_key="EMPTY")
    try:
        models = client.models.list()
        served_models = {m.id for m in models.data}
        print(f"✅ Connected to vLLM at {args.vllm_api_base}")
        if args.judge_model not in served_models:
            raise RuntimeError(
                f"judge_model='{args.judge_model}' not found in served models. Available: {sorted(served_models)}"
            )
    except Exception as exc:
        raise RuntimeError(f"Failed to connect to vLLM server: {exc}")

    dspy_lm = None
    if args.method == "dspy_opt_prompt":
        dspy_lm = VLLMChatLM(
            client=client,
            model=args.judge_model,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            top_p=args.top_p,
            message_style=model_profile.get("message_style", "user_only"),
            content_style=model_profile.get("content_style", "openai_text_list"),
            merge_system_into_user=bool(model_profile.get("merge_system_into_user", True)),
            system_role="",
            json_object=True,
            disable_thinking=not bool(args.allow_thinking),
            sanitize_json_output=True,
        )

    stage1 = load_stage1_module()
    retrieval_runtime = init_retrieval_runtime(args, stage1)
    retrieval_sema = threading.Semaphore(max(1, args.retrieval_inflight))
    vllm_sema = threading.Semaphore(max(1, args.vllm_inflight))

    skill_files = discover_skills(args.skills_dir, excluded_list=args.excluded_skills)
    if not skill_files:
        print(f"⚠️ No skills found in {args.skills_dir}")
        return

    print(f"📝 Found {len(skill_files)} skill file(s)")
    stamp = args.run_stamp if args.run_stamp else now_run_stamp()
    out_dir = os.path.join(
        args.output_root,
        f"dataset={args.dataset_id}",
        C5_DOMAIN,
        f"method={args.method}",
        "backend=vllm",
        f"model={args.judge_model}",
        f"evidence={args.evidence_tag}",
        f"variation={args.method}",
        f"run={stamp}",
    )
    os.makedirs(out_dir, exist_ok=True)

    best_skill_cache: Dict[Tuple[str, str], Optional[str]] = {}
    total_processed = 0
    total_skipped = 0
    overall_t0 = time.time()

    for skill_file in skill_files:
        skill_filename = os.path.basename(skill_file)
        skill_version = extract_version_from_filename(skill_filename)
        skill_text = read_text(skill_file)
        skill_hash = md5_text(skill_text)
        out_name = f"{C5_SKILL_DIR_NAME}_{skill_version}.jsonl" if skill_version else f"{C5_SKILL_DIR_NAME}.jsonl"
        out_path = os.path.join(out_dir, out_name)
        seen = set() if args.force else read_existing_record_ids(out_path)

        opt_prompt_skill_text = None
        if args.method == "dspy_opt_prompt":
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
                    backend="vllm",
                )
                if best_skill_path:
                    try:
                        best_skill_cache[cache_key] = read_text(best_skill_path)
                    except Exception:
                        best_skill_cache[cache_key] = None
                else:
                    best_skill_cache[cache_key] = None
            opt_prompt_skill_text = best_skill_cache.get(cache_key)

        print(f"\n📝 Skill: {skill_filename} (version={skill_version or 'default'})")
        print(f"   Output: {out_path}")
        print(f"   Resume: {'ON' if seen else 'OFF'}")

        processed = 0
        skipped = 0
        for row in rows:
            row_t0 = time.time()
            qa_id = get_qa_id(row)
            record_id = build_record_id(row)
            if not args.force and str(record_id) in seen:
                skipped += 1
                continue

            question = safe_str(row.get("question", "")).strip()
            answer = safe_str(row.get("chatbot_answer") or row.get("answer", "")).strip()

            ctx_used, retrieval_meta_used, retrieval_status, context_missing_reason, retrieval_dt = retrieve_inline_context(
                row=row,
                args=args,
                stage1=stage1,
                retrieval_runtime=retrieval_runtime,
                retrieval_sema=retrieval_sema,
            )

            if not question or not answer:
                out_rec = build_output_record(
                    row=row,
                    record_id=record_id,
                    qa_id=qa_id,
                    skill_file=skill_file,
                    skill_filename=skill_filename,
                    skill_version=skill_version,
                    skill_hash=skill_hash,
                    args=args,
                    ctx_used=ctx_used,
                    retrieval_meta_used=retrieval_meta_used,
                    retrieval_status=retrieval_status,
                    context_missing_reason="empty_question_or_answer",
                    retrieval_latency_s=retrieval_dt,
                    judge_result={
                        "ai_reasoning": "",
                        "ai_score": -1,
                        "raw_output": "",
                        "parse_error": None,
                        "error": "empty_question_or_answer",
                        "finish_reason": None,
                    },
                    judge_latency_s=None,
                    total_latency_s=time.time() - row_t0,
                    retrieval_settings=retrieval_runtime["retrieval_settings"],
                )
                append_jsonl(out_path, out_rec)
                seen.add(str(record_id))
                processed += 1
                continue

            if args.method == "dspy_opt_prompt" and opt_prompt_skill_text:
                skill_for_run = opt_prompt_skill_text
            else:
                skill_for_run = skill_text

            if args.method == "agent":
                user_prompt = render_user_text(skill_for_run, ctx_used, question, answer)
                judge_result = call_vllm_chat_strict(
                    client=client,
                    model=args.judge_model,
                    model_profile=model_profile,
                    system_role="",
                    user_prompt=user_prompt,
                    args=args,
                    vllm_sema=vllm_sema,
                )
            else:
                if opt_prompt_skill_text is None:
                    judge_result = {
                        "ai_reasoning": "",
                        "ai_score": -1,
                        "raw_output": "",
                        "parse_error": None,
                        "error": "No compiled best_skill for this criterion/version",
                        "latency_s": None,
                        "finish_reason": None,
                    }
                else:
                    judge_result = call_dspy_vllm_noopt(
                        lm=dspy_lm,
                        skill_text=opt_prompt_skill_text,
                        question=question,
                        answer=answer,
                        criterion_id=C5_CRITERION_ID,
                        context=ctx_used,
                    )
                    judge_result["finish_reason"] = None

            out_rec = build_output_record(
                row=row,
                record_id=record_id,
                qa_id=qa_id,
                skill_file=skill_file,
                skill_filename=skill_filename,
                skill_version=skill_version,
                skill_hash=skill_hash,
                args=args,
                ctx_used=ctx_used,
                retrieval_meta_used=retrieval_meta_used,
                retrieval_status=retrieval_status,
                context_missing_reason=context_missing_reason,
                retrieval_latency_s=retrieval_dt,
                judge_result=judge_result,
                judge_latency_s=judge_result.get("latency_s"),
                total_latency_s=time.time() - row_t0,
                retrieval_settings=retrieval_runtime["retrieval_settings"],
            )
            append_jsonl(out_path, out_rec)
            seen.add(str(record_id))
            processed += 1

        print(f"   Processed: {processed} | Skipped: {skipped}")
        total_processed += processed
        total_skipped += skipped

    elapsed = time.time() - overall_t0
    print(f"\n✅ Done! Processed: {total_processed} | Skipped: {total_skipped} | Time: {elapsed:.1f}s")
    print(f"   Output: {out_dir}")


if __name__ == "__main__":
    main()
