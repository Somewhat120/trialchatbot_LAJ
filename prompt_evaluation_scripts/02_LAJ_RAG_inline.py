#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
02_LAJ_RAG_inline.py
--------------------
Inline C5 RAG prototype for vLLM-compatible models.

Differences from 02_LAJ_RAG_loop.py:
- Performs retrieval inline instead of consuming precomputed retrieved_context.
- Writes to a separate output root by default.
- Records split latency fields for retrieval, judge, and total.

This runner stays model/profile driven and only targets prompt_base=5_factual_acc.
"""

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI
from tqdm import tqdm

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("MKL_NUM_THREADS", "2"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", os.getenv("OPENBLAS_NUM_THREADS", "2"))
os.environ.setdefault("NUMEXPR_NUM_THREADS", os.getenv("NUMEXPR_NUM_THREADS", "2"))

write_lock = threading.Lock()

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PROMPTS_ROOT = REPO_ROOT / "prompts" / "prompt_comparison_accuracy"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "outputs" / "LAJ_score_outputs_inline"
DEFAULT_MODEL_PROFILES_JSON = REPO_ROOT / "configs" / "model_profiles.json"

PROMPT_VARIATIONS = {
    "def_cot": {"dir": str(PROMPTS_ROOT / "prompts_cot")},
    "def_only": {"dir": str(PROMPTS_ROOT / "prompts_def_only")},
    "baseline": {"dir": str(PROMPTS_ROOT / "baseline")},
}

OUTPUT_ROOT = str(DEFAULT_OUTPUT_ROOT)
DEMONSTRATIONS_DIR = str(PROMPTS_ROOT / "demonstrations")
DEFAULT_EVIDENCE_TAG = "inline_icf_chunks_k5"
DEFAULT_PROMPT_BASE = "5_factual_acc"
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
TEST_MODE = False

PROMPTBASE_TO_CRITERION_ID = {
    "5_factual_acc": "reliability_c5_factual_accuracy",
}

CRITERION_ID_TO_DOMAIN = {
    "reliability_c5_factual_accuracy": "reliability",
}

THOUGHT_RE = re.compile(
    r"<(?:unused94|think|start_of_thought)>\s*(.*?)\s*<(?:unused95|/think|end_of_thought)>",
    flags=re.DOTALL | re.IGNORECASE,
)
THOUGHT_UNCLOSED_RE = re.compile(
    r"<(?:unused94|think|start_of_thought)>\s*(.*)\Z",
    flags=re.DOTALL | re.IGNORECASE,
)


def run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def md5_json(obj: Any) -> str:
    return md5_text(json.dumps(obj, ensure_ascii=False, sort_keys=True))


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(filepath, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(filepath: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with write_lock:
        with open(filepath, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def is_valid_completed_record(rec: Dict[str, Any]) -> bool:
    if rec.get("status") != "ok":
        return False
    if rec.get("parse_error"):
        return False
    score = rec.get("ai_score")
    try:
        score_int = int(float(score))
    except (TypeError, ValueError):
        return False
    return score_int != -1


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
            if rid:
                seen.add(str(rid))
    return seen


def get_row_id(row: Dict[str, Any]) -> str:
    qa_id = row.get("qa_id")
    if qa_id is not None and str(qa_id).strip():
        return str(qa_id).strip()
    trial = row.get("trial_num") or "UNKNOWN"
    qn = row.get("question_num") or row.get("question_num_raw_source", "UNKNOWN")
    return f"{str(trial).strip()}::{qn}"


def discover_prompts(prompts_dir: str, excluded_list: List[str]) -> List[str]:
    if not os.path.exists(prompts_dir):
        return []
    files = glob.glob(os.path.join(prompts_dir, "*.json"))
    files = [path for path in files if os.path.basename(path) not in excluded_list]
    files.sort()
    return files


def extract_version_from_filename(filename: str) -> Optional[str]:
    base_name = os.path.splitext(filename)[0]
    match = re.search(r"_v(\d+)$", base_name)
    return f"v{match.group(1)}" if match else None


def get_base_prompt_name(filename: str) -> str:
    base_name = os.path.splitext(filename)[0]
    return re.sub(r"_v\d+$", "", base_name)


def load_demonstrations(prompt_name: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{prompt_name}_demos.json")
    if os.path.exists(demo_file):
        try:
            with open(demo_file, "r", encoding="utf-8") as handle:
                return (json.load(handle).get("demonstrations", []), demo_file)
        except Exception:
            pass
    base_name = re.sub(r"_short_v\d+$", "", prompt_name)
    base_name = re.sub(r"_v\d+$", "", base_name)
    if base_name != prompt_name:
        demo_file = os.path.join(DEMONSTRATIONS_DIR, f"{base_name}_demos.json")
        if os.path.exists(demo_file):
            try:
                with open(demo_file, "r", encoding="utf-8") as handle:
                    return (json.load(handle).get("demonstrations", []), demo_file)
            except Exception:
                pass
    return ([], None)


def format_demonstrations(demos: List[Dict[str, Any]]) -> str:
    if not demos:
        return ""
    lines = ["\n\n### EXAMPLES\n"]
    for idx, demo in enumerate(demos, 1):
        lines.append(f"Example {idx}:")
        lines.append(f"Trial: {demo.get('trial_num', 'N/A')}")
        lines.append(f"User Question: {demo.get('question', 'N/A')}")
        lines.append(f"Chatbot Answer: {demo.get('answer', 'N/A')}")
        reasoning = demo.get("ai_reasoning", "")
        reasoning = "" if reasoning is None else str(reasoning)
        reasoning_json = json.dumps(reasoning, ensure_ascii=False)
        score = demo.get("final_score", "N/A")
        lines.append(
            f"Expected JSON Output: {{\"reasoning\": {reasoning_json}, \"final_score\": {score}}}\n"
        )
    return "\n".join(lines).strip()


def inject_demos(user_prompt: str, demo_text: str) -> Tuple[str, str]:
    if not demo_text or not demo_text.strip():
        return (user_prompt, "NO_DEMOS")
    anchors = [
        ("### SOURCE TEXT", "SOURCE_TEXT"),
        ("### OUTPUT (STRICT JSON ONLY)", "OUTPUT_STRICT"),
    ]
    for anchor, label in anchors:
        if anchor in user_prompt:
            idx = user_prompt.find(anchor)
            updated = user_prompt[:idx] + demo_text.strip() + "\n\n" + user_prompt[idx:]
            return (updated, label)
    return (user_prompt + "\n\n" + demo_text.strip(), "APPEND_FALLBACK")


def strip_think_tags(text: str) -> str:
    if not isinstance(text, str):
        return text
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


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part for part in parts if part]


def truncate_reasoning_text(text: str, max_chars: int, max_sentences: int) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if max_sentences > 0:
        sents = _split_sentences(text)
        if len(sents) > max_sentences:
            text = " ".join(sents[:max_sentences]).strip()
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def truncate_text(text: str, max_chars: int) -> str:
    if not text or max_chars <= 0:
        return text
    return text if len(text) <= max_chars else text[:max_chars]


def reorder_template_qa_before_evidence(template: str) -> str:
    pattern = (
        "### SOURCE TEXT (Informed Consent Form)\n{context}\n\n"
        "### USER QUESTION\n{question}\n\n"
        "### CHATBOT ANSWER\n{answer}\n\n"
    )
    replacement = (
        "### USER QUESTION\n{question}\n\n"
        "### CHATBOT ANSWER\n{answer}\n\n"
        "### SOURCE TEXT (Informed Consent Form)\n{context}\n\n"
    )
    if pattern in template:
        return template.replace(pattern, replacement, 1)
    return template


_EVIDENCE_BLOCK_RE = re.compile(
    r"^=== \[EVIDENCE \d+ .*?(?=^=== \[EVIDENCE \d+ |\Z)",
    flags=re.MULTILINE | re.DOTALL,
)


def trim_evidence_blocks(text: str, max_blocks: int) -> str:
    if not isinstance(text, str) or max_blocks <= 0:
        return text
    blocks = _EVIDENCE_BLOCK_RE.findall(text)
    if not blocks:
        return text
    return "\n\n".join(blocks[:max_blocks]).strip()


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def apply_strict_json_contract(user_prompt: str, stop_marker: str) -> str:
    appendix = STRICT_JSON_APPENDIX_TEMPLATE.format(stop_marker=stop_marker)
    return f"{user_prompt.rstrip()}{appendix}"


def apply_compact_retry_contract(user_prompt: str, stop_marker: str) -> str:
    appendix = COMPACT_RETRY_APPENDIX.format(stop_marker=stop_marker)
    return f"{user_prompt.rstrip()}{appendix}"


def _find_json_object(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(text, str):
        return None, None
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).replace("```", "").strip()
    if not cleaned or "{" not in cleaned or "}" not in cleaned:
        return None, None
    left_positions = [i for i, char in enumerate(cleaned) if char == "{"]
    right_positions = [i for i, char in enumerate(cleaned) if char == "}"]
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
        r'final\s*_?\s*score[^0-9-]{0,20}(-?\d{1,2})',
        normalized,
        flags=re.IGNORECASE,
    )
    if match:
        return int(match.group(1)), None, normalized
    return -1, "missing_json", normalized


def build_messages_from_profile(
    profile: Dict[str, Any],
    system_text: str,
    formatted_user_text: str,
) -> List[Dict[str, Any]]:
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


def load_stage1_module():
    stage1_path = Path(__file__).with_name("01_retrieval_chunk.py")
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
        message = str(exc)
        if ("additional_chat_templates" in message) or ("RemoteEntryNotFoundError" in message) or ("404" in message):
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

    valid_trial_ids = build_valid_trial_ids(args.db_folder)
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
    }
    return {
        "db": db,
        "reranker": reranker,
        "bm25_cache": {},
        "valid_trial_ids": valid_trial_ids,
        "retrieval_settings": retrieval_settings,
    }


def normalize_retrieval_record(row: Dict[str, Any]) -> Dict[str, Any]:
    rec = dict(row)
    if "answer" not in rec or not str(rec.get("answer", "")).strip():
        rec["answer"] = rec.get("chatbot_answer", "")
    if "Level of factual accuracy" not in rec and "human_score" in rec:
        rec["Level of factual accuracy"] = rec.get("human_score")
    return rec


def row_belongs_to_prompt(row: Dict[str, Any], prompt_base: str) -> bool:
    return prompt_base == DEFAULT_PROMPT_BASE


def process_one(job):
    (
        row,
        prompt_data,
        prompt_base,
        prompt_hash,
        variation,
        args,
        client,
        vllm_sema,
        retrieval_sema,
        model_profile,
        demo_text,
        demos_meta,
        stage1,
        retrieval_runtime,
    ) = job

    total_t0 = time.time()

    row_id = get_row_id(row)
    trial = str(row.get("trial_num", "UNKNOWN")).strip()
    qn = row.get("question_num")
    question = str(row.get("question", "")).strip()
    answer = str(row.get("chatbot_answer", row.get("answer", ""))).strip()

    retrieval_input = normalize_retrieval_record(row)
    retrieval_t0 = time.time()
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
    retrieval_dt = time.time() - retrieval_t0

    raw_context = str(retrieval_obj.get("retrieved_context", "") or "").strip()
    if args.trim_evidence_to_final_k:
        raw_context = trim_evidence_blocks(raw_context, args.final_k)
    if args.max_context_chars > 0:
        raw_context = truncate_text(raw_context, args.max_context_chars).strip()
    has_context = bool(raw_context)

    retrieval_status = retrieval_obj.get("status", "unknown")
    context_missing_reason = "context_present"
    if not has_context:
        context_missing_reason = retrieval_status

    template = prompt_data.get("instruction_template", "")
    if args.qa_before_evidence:
        template = reorder_template_qa_before_evidence(template)
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
                flags=re.IGNORECASE,
            )

    user_prompt = template.replace("{context}", context_message).replace("{question}", question).replace("{answer}", answer)
    user_prompt, demos_anchor_used = inject_demos(user_prompt, demo_text)
    strict_user_prompt = apply_strict_json_contract(user_prompt, args.json_stop_marker) if args.strict_json_mode else user_prompt
    compact_retry_prompt = apply_compact_retry_contract(user_prompt, args.json_stop_marker) if args.strict_json_mode else user_prompt

    messages = build_messages_from_profile(
        model_profile,
        prompt_data.get("system_role", ""),
        strict_user_prompt,
    )
    compact_messages = build_messages_from_profile(
        model_profile,
        prompt_data.get("system_role", ""),
        compact_retry_prompt,
    )

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

    judge_t0 = time.time()
    text = ""
    hidden_thoughts, clean_json, score = "", "", -1
    status, err = "error", None
    parse_error = None
    finish_reason = None
    no_json_mode = bool(args.no_json_mode)
    prev_parse_error = None
    prev_finish_reason = None
    for attempt in range(max(0, args.max_retries) + 1):
        use_compact_retry = (
            args.strict_json_mode and attempt > 0 and (
                prev_finish_reason == "length"
                or prev_parse_error in {"truncated_json", "missing_json", "malformed_json", "no_score_found"}
            )
        )
        req_messages = compact_messages if use_compact_retry else messages
        effective_max_tokens = args.max_tokens
        if use_compact_retry:
            effective_max_tokens = min(args.max_tokens, max(32, args.retry_max_tokens))
        try:
            req_kwargs: Dict[str, Any] = {
                "model": args.model,
                "messages": req_messages,
                "temperature": args.temperature,
                "top_p": args.top_p,
                "max_tokens": effective_max_tokens,
                "extra_body": extra_body if extra_body else None,
            }
            if args.strict_json_mode and args.json_stop_marker:
                req_kwargs["stop"] = [args.json_stop_marker]
            if args.strict_json_mode and not no_json_mode:
                req_kwargs["response_format"] = {"type": "json_object"}
            with vllm_sema:
                resp = client.chat.completions.create(**req_kwargs)
            text = strip_think_tags(safe_str(resp.choices[0].message.content))
            finish_reason = safe_str(getattr(resp.choices[0], "finish_reason", None)) or None
            prev_finish_reason = finish_reason
            hidden_thoughts, _ = parse_reasoning_and_json(text)
            score, parse_error, clean_json = extract_score(text, args.json_stop_marker if args.strict_json_mode else None)
            prev_parse_error = parse_error
            if score != -1:
                status, err = "ok", None
                break
            status, err = "ok", None
            if attempt < max(0, args.max_retries):
                time.sleep(max(0.0, args.retry_delay_s))
                continue
        except Exception as exc:
            err = str(exc)
            if args.strict_json_mode and (not no_json_mode) and ("response_format" in err.lower() or "json" in err.lower()):
                no_json_mode = True
            text = ""
            hidden_thoughts, clean_json, score = "", "", -1
            parse_error = "backend_error"
            if attempt < max(0, args.max_retries):
                time.sleep(max(0.0, args.retry_delay_s))
                continue
            break
    judge_dt = time.time() - judge_t0
    total_dt = time.time() - total_t0

    reasoning_from_json = ""
    if not hidden_thoughts:
        try:
            left, right = clean_json.find("{"), clean_json.rfind("}")
            if left != -1 and right != -1 and right > left:
                obj = json.loads(clean_json[left:right + 1])
                reasoning_from_json = obj.get("reasoning", "") or ""
        except Exception:
            reasoning_from_json = ""

    ai_reasoning = hidden_thoughts or reasoning_from_json or clean_json
    if (hidden_thoughts or reasoning_from_json) and (args.max_reasoning_chars or args.max_reasoning_sentences):
        ai_reasoning = truncate_reasoning_text(
            ai_reasoning,
            max_chars=args.max_reasoning_chars,
            max_sentences=args.max_reasoning_sentences,
        )

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
        "retrieval_status": retrieval_status,
        "evidence_tag": args.evidence_tag,
        "retrieved_context": context_message,
        "retrieval_meta": retrieval_obj.get("retrieval_meta", []),
        "retrieval_settings": retrieval_runtime["retrieval_settings"],
        "judge_model": args.model,
        "backend": "vllm",
        "model_profile": args.model_profile,
        "vllm_api_base": args.vllm_api_base,
        "generation_config": {
            "temperature": args.temperature,
            "top_p": args.top_p,
            "max_tokens": args.max_tokens,
            "allow_thinking": bool(args.allow_thinking),
        },
        "ai_reasoning": ai_reasoning,
        "ai_score": score,
        "parse_error": parse_error if score == -1 and status == "ok" else None,
        "error_msg": err,
        "finish_reason": finish_reason,
        "latency_s": round(judge_dt, 4),
        "judge_latency_s": round(judge_dt, 4),
        "retrieval_latency_s": round(retrieval_dt, 4),
        "total_latency_s": round(total_dt, 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_jsonl", required=True)
    parser.add_argument("--dataset_id", required=True)
    parser.add_argument("--prompt_variation", type=str, default=None)
    parser.add_argument("--prompt_base", type=str, default=DEFAULT_PROMPT_BASE)

    parser.add_argument("--vllm_api_base", type=str, default=os.getenv("VLLM_API_BASE", "http://localhost:8000/v1"))
    parser.add_argument("--model", type=str, default=os.getenv("VLLM_MODEL_NAME", "qwen3-30b"))
    parser.add_argument("--model_profiles_json", type=str, default=os.getenv("MODEL_PROFILES_JSON", str(DEFAULT_MODEL_PROFILES_JSON)))
    parser.add_argument("--model_profile", type=str, default=os.getenv("MODEL_PROFILE", "qwen3_30b_a3b_instruct_vllm"))

    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top_p", type=float, default=1.0)
    parser.add_argument("--max_tokens", type=int, default=1024)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--vllm_inflight", type=int, default=4)
    parser.add_argument("--retrieval_inflight", type=int, default=1)
    parser.add_argument("--max_reasoning_chars", type=int, default=1024)
    parser.add_argument("--max_reasoning_sentences", type=int, default=3)
    parser.add_argument("--max_context_chars", type=int, default=0)
    parser.add_argument("--strict_json_mode", action="store_true")
    parser.add_argument("--json_stop_marker", type=str, default=DEFAULT_JSON_STOP_MARKER)
    parser.add_argument("--no_json_mode", action="store_true")
    parser.add_argument("--retry_max_tokens", type=int, default=320)
    parser.add_argument("--max_retries", type=int, default=1)
    parser.add_argument("--retry_delay_s", type=float, default=0.5)

    parser.add_argument("--force", action="store_true")
    parser.add_argument("--debug_payload", action="store_true")
    parser.add_argument("--run_stamp", type=str, default=None)
    parser.add_argument("--output_root", type=str, default=str(DEFAULT_OUTPUT_ROOT))

    parser.add_argument("--zeroshot_only", action="store_true")
    parser.add_argument("--fewshot_only", action="store_true")
    parser.add_argument("--test_n", type=int, default=None)
    parser.add_argument("--evidence_tag", type=str, default=DEFAULT_EVIDENCE_TAG)
    parser.add_argument("--allow_thinking", action="store_true")
    parser.add_argument("--qa_before_evidence", action="store_true")
    parser.add_argument("--trim_evidence_to_final_k", action="store_true")

    parser.add_argument("--db_folder", type=str, required=True, help="Directory containing the Chroma DB used for inline retrieval")
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

    profiles = load_json(args.model_profiles_json)
    model_profile = profiles[args.model_profile]

    stage1 = load_stage1_module()
    retrieval_runtime = init_retrieval_runtime(args, stage1)

    client = OpenAI(base_url=args.vllm_api_base, api_key="EMPTY")
    vllm_sema = threading.Semaphore(max(1, args.vllm_inflight))
    retrieval_sema = threading.Semaphore(max(1, args.retrieval_inflight))

    rows = load_jsonl(args.input_jsonl)
    if args.test_n is not None:
        rows = rows[:args.test_n]
    elif TEST_MODE:
        rows = rows[:5]

    stamp = args.run_stamp if args.run_stamp else run_stamp()
    variations_to_run = {args.prompt_variation: PROMPT_VARIATIONS[args.prompt_variation]} if args.prompt_variation else PROMPT_VARIATIONS

    for variation, vcfg in variations_to_run.items():
        prompt_paths = discover_prompts(vcfg["dir"], [])
        for prompt_path in prompt_paths:
            prompt_filename = os.path.basename(prompt_path)
            prompt_base = get_base_prompt_name(prompt_filename)
            if args.prompt_base and prompt_base != args.prompt_base:
                continue
            if prompt_base not in PROMPTBASE_TO_CRITERION_ID:
                continue

            prompt_data = load_json(prompt_path)
            prompt_data["_prompt_filename"] = prompt_filename
            prompt_hash = md5_json(prompt_data)

            demos, demos_file = load_demonstrations(os.path.splitext(prompt_filename)[0])
            demo_text = format_demonstrations(demos)
            num_demos = len(demos)
            demos_meta = {
                "num_demos": num_demos,
                "demos_file": demos_file,
                "demos_hash": md5_json({"demonstrations": demos}) if demos else None,
            }
            if args.zeroshot_only:
                demo_text = ""
                method_segment = "prompt_zeroshot"
                demos_meta = {**demos_meta, "num_demos": 0}
            else:
                method_segment = "prompt_fewshot" if num_demos > 0 else "prompt_zeroshot"
            if args.fewshot_only and num_demos == 0:
                continue

            rows_for_prompt = [row for row in rows if row_belongs_to_prompt(row, prompt_base)]
            if not rows_for_prompt:
                continue

            criterion_id = PROMPTBASE_TO_CRITERION_ID[prompt_base]
            domain = CRITERION_ID_TO_DOMAIN[criterion_id]
            criterion_name = criterion_id.split("_", 1)[1] if "_" in criterion_id else criterion_id

            out_dir = os.path.join(
                args.output_root,
                f"dataset={args.dataset_id}",
                domain,
                f"method={method_segment}",
                "backend=vllm",
                f"model={args.model}",
                f"evidence={args.evidence_tag}",
                f"variation={variation}",
                f"run={stamp}",
            )
            os.makedirs(out_dir, exist_ok=True)

            ver = extract_version_from_filename(prompt_filename)
            out_path = os.path.join(out_dir, f"{criterion_name}_{ver}.jsonl" if ver else f"{criterion_name}.jsonl")

            seen = read_existing_record_ids(out_path) if not args.force else set()
            jobs = [
                (
                    row,
                    prompt_data,
                    prompt_base,
                    prompt_hash,
                    variation,
                    args,
                    client,
                    vllm_sema,
                    retrieval_sema,
                    model_profile,
                    demo_text,
                    demos_meta,
                    stage1,
                    retrieval_runtime,
                )
                for row in rows_for_prompt
                if get_row_id(row) not in seen
            ]

            if not jobs:
                continue

            print(f"📝 {prompt_filename} | processing {len(jobs)} records -> {out_path}")
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(process_one, job) for job in jobs]
                try:
                    completed = tqdm(as_completed(futures), total=len(futures), leave=False, file=sys.stderr)
                except (OSError, AttributeError):
                    completed = as_completed(futures)
                for fut in completed:
                    append_jsonl(out_path, fut.result())


if __name__ == "__main__":
    main()
