#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
stage2_laj_runner.py
--------------------
Stage 2: LAJ (LLM-as-Judge) runner.
Consumes Stage 1 frozen retrieval JSONL (evidence already selected) and produces
LAJ scoring JSONL via vLLM OpenAI-compatible Chat Completions.

Design goals
- Lightweight: NO embeddings, NO Chroma, NO BM25/SPLADE/RRF, NO rerank.
- Resume-safe: append-only JSONL; skips already-scored run_keys unless --force.
- Model-family safe: message formatting controlled by a JSON model profile (no rewrites).
- Easy to debug: run_key + record_id + optional payload previews/hashes.

NEW (output layout)
- Writes outputs into per-variation folders like:
    <out_root>/LAJ_score_QWEN_RAG_baseline/
    <out_root>/LAJ_score_QWEN_RAG_def_only/
    <out_root>/LAJ_score_QWEN_RAG_def_cot/
- Writes ONE output JSONL per prompt file under that folder.
"""

import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", os.getenv("OMP_NUM_THREADS", "2"))
os.environ.setdefault("MKL_NUM_THREADS", os.getenv("MKL_NUM_THREADS", "2"))
os.environ.setdefault("OPENBLAS_NUM_THREADS", os.getenv("OPENBLAS_NUM_THREADS", "2"))
os.environ.setdefault("NUMEXPR_NUM_THREADS", os.getenv("NUMEXPR_NUM_THREADS", "2"))

import json
import re
import glob
import time
import argparse
import hashlib
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm
from openai import OpenAI


# ---------------- thread-safety ----------------
write_lock = threading.Lock()

# ------------------------------------------------
# Small helpers
# ------------------------------------------------
def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)

def safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def md5_text(x: str) -> str:
    return hashlib.md5(x.encode("utf-8")).hexdigest()

def slug(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s[:max_len] if len(s) > max_len else s

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def read_jsonl(path: str):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    with write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def stage1_basename(stage1_jsonl: str) -> str:
    # "path/to/foo.jsonl" -> "foo"
    base = os.path.basename(stage1_jsonl)
    if base.endswith(".jsonl"):
        base = base[:-5]
    return base

def prompt_basename(prompt_file: str) -> str:
    # "5_factual_acc.json" -> "5_factual_acc"
    base = os.path.basename(prompt_file)
    if base.endswith(".json"):
        base = base[:-5]
    return base

def build_record_id(nid: str, qn: Any) -> str:
    return f"{nid}::{qn}"

def build_run_key(
    nid: str,
    qn: Any,
    prompt_variation: str,
    prompt_file: str,
    judge_model: str,
    final_k_used: int,
    model_profile: str,
) -> str:
    return f"{nid}::{qn}::{prompt_variation}::{prompt_file}::{judge_model}::{model_profile}::k{final_k_used}"

def load_existing_run_keys(out_jsonl: str) -> set:
    keys = set()
    if not os.path.exists(out_jsonl):
        return keys
    for obj in read_jsonl(out_jsonl):
        rk = obj.get("run_key")
        if rk:
            keys.add(rk)
    return keys

# ------------------------------------------------
# Evidence truncation for Stage 2 (final_k_used)
# ------------------------------------------------
_EVIDENCE_SPLIT_RE = re.compile(r"\n(?=== \[EVIDENCE\s+\d+\s*)")

def truncate_retrieved_context(ctx: str, final_k_used: int) -> str:
    ctx = safe_str(ctx).strip()
    if not ctx:
        return ""

    parts = _EVIDENCE_SPLIT_RE.split(ctx)
    if len(parts) == 1:
        parts2 = re.split(r"(?=== \[EVIDENCE\s+\d+\s*)", ctx)
        parts2 = [p.strip() for p in parts2 if p.strip()]
        if len(parts2) <= 1:
            return ctx
        parts = parts2

    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return ctx

    k = max(1, int(final_k_used))
    return "\n\n".join(parts[:k])

# ------------------------------------------------
# Prompt loading
# ------------------------------------------------
@dataclass
class PromptSpec:
    variation: str
    file_path: str
    file_name: str
    system_role: str
    instruction_template: str
    prompt_hash: str

def parse_prompt_dirs(prompt_dirs: List[str]) -> Dict[str, str]:
    out = {}
    for item in prompt_dirs:
        if ":" not in item:
            raise ValueError(f"--prompt_dirs entry must be variation:dir, got: {item}")
        var, d = item.split(":", 1)
        out[var.strip()] = d.strip()
    return out

def discover_prompt_files(dir_path: str, excluded: Optional[List[str]] = None) -> List[str]:
    if not os.path.exists(dir_path):
        return []
    files = sorted(glob.glob(os.path.join(dir_path, "*.json")))
    if excluded:
        ex = set(excluded)
        files = [f for f in files if os.path.basename(f) not in ex]
    return files

def load_prompts(prompt_dir_map: Dict[str, str], excluded: Optional[List[str]] = None) -> List[PromptSpec]:
    prompts: List[PromptSpec] = []
    for variation, d in prompt_dir_map.items():
        for fp in discover_prompt_files(d, excluded=excluded):
            obj = load_json(fp)
            system_role = safe_str(obj.get("system_role", "")).strip()
            instruction_template = safe_str(obj.get("instruction_template", "")).strip()
            ph = md5_text(system_role + "\n\n" + instruction_template)
            prompts.append(
                PromptSpec(
                    variation=variation,
                    file_path=fp,
                    file_name=os.path.basename(fp),
                    system_role=system_role,
                    instruction_template=instruction_template,
                    prompt_hash=ph,
                )
            )
    return prompts

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

# ------------------------------------------------
# Model profile loading + message building
# ------------------------------------------------
def load_model_profiles(path: str) -> dict:
    profiles = load_json(path)
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"Model profiles file must be a non-empty dict: {path}")
    return profiles

def build_messages_from_profile(
    profile: dict,
    prompt: PromptSpec,
    context: str,
    question: str,
    answer: str
) -> List[Dict[str, Any]]:
    """
    Supports:
      - message_style: system_user OR user_only
      - content_style: string OR openai_text_list
      - merge_system_into_user: bool
    """
    user_text = render_user_text(prompt.instruction_template, context, question, answer)

    system_text = prompt.system_role.strip() if prompt.system_role else ""
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
            # ensure system instructions are not lost
            if content_style == "openai_text_list":
                user_content = [{"type": "text", "text": f"{system_text}\n\n{combined_user}".strip()}]
            else:
                user_content = f"{system_text}\n\n{combined_user}".strip()
        messages.append({"role": "user", "content": user_content})
    else:
        raise ValueError(f"Unknown message_style in profile: {msg_style}")

    return messages

# ------------------------------------------------
# Score extraction
# ------------------------------------------------
_FINAL_SCORE_RE = re.compile(r'final_score[^0-9]{0,20}(\d)', flags=re.IGNORECASE)

def extract_score(text: str) -> int:
    s = safe_str(text).strip()
    if not s:
        return -1
    try:
        l = s.find("{")
        r = s.rfind("}")
        if l != -1 and r != -1 and r > l:
            sub = s[l:r+1]
            obj = json.loads(sub)
            if isinstance(obj, dict) and "final_score" in obj:
                return int(obj["final_score"])
    except Exception:
        pass
    m = _FINAL_SCORE_RE.search(s)
    return int(m.group(1)) if m else -1

# ------------------------------------------------
# Stage1 meta path
# ------------------------------------------------
def infer_stage1_meta_path(stage1_jsonl: str) -> str:
    cand = stage1_jsonl + ".meta.json"
    return cand if os.path.exists(cand) else ""

# ------------------------------------------------
# Extract chunk resource from stage1_jsonl path
# ------------------------------------------------
def extract_chunk_resource(stage1_jsonl: str) -> str:
    """
    Extract chunk resource (internal, add_round1, etc.) from stage1_jsonl path.
    Example: retrieval_chunk/internal/short/file.jsonl -> internal
    Example: retrieval_chunk/add_round1/short/file.jsonl -> add_round1
    Returns empty string if not found.
    """
    path_parts = os.path.normpath(stage1_jsonl).split(os.sep)
    try:
        # Find 'retrieval_chunk' in path and get the next component
        idx = path_parts.index("retrieval_chunk")
        if idx + 1 < len(path_parts):
            return path_parts[idx + 1]
    except ValueError:
        pass
    return ""

# ------------------------------------------------
# Output folder naming (your requested layout)
# ------------------------------------------------
def resolve_variation_out_dir(out_root: str, template: str, model_tag: str, variation: str, chunk_resource: str = "") -> str:
    """
    template examples:
      LAJ_score_{model_tag}_RAG_{variation}
      LAJ_score_{model_tag}_{variation}     (if you prefer no RAG in name)
    
    If chunk_resource is provided, it's added as a subdirectory.
    Final structure: out_root/LAJ_score_.../chunk_resource/
    """
    d = template.format(model_tag=model_tag, variation=variation)
    base_dir = os.path.join(out_root, d)
    if chunk_resource:
        return os.path.join(base_dir, chunk_resource)
    return base_dir

def resolve_output_paths(
    out_root: str,
    out_dir_template: str,
    model_tag: str,
    prompt: PromptSpec,
    stage1_jsonl: str,
    judge_model: str,
    model_profile: str,
    final_k_used: int,
) -> Tuple[str, str]:
    """
    Returns:
      out_jsonl, out_meta_json
    """
    # Extract chunk resource from stage1_jsonl path
    chunk_resource = extract_chunk_resource(stage1_jsonl)
    
    out_dir = resolve_variation_out_dir(out_root, out_dir_template, model_tag, prompt.variation, chunk_resource)
    ensure_dir(out_dir)

    s1 = slug(stage1_basename(stage1_jsonl), 80)
    pf = slug(prompt_basename(prompt.file_name), 60)
    jm = slug(judge_model, 40)
    mp = slug(model_profile, 40)

    # Deterministic filename (no timestamp) to support resume.
    fname = f"{s1}__{pf}__{jm}__{mp}__k{final_k_used}.jsonl"
    out_jsonl = os.path.join(out_dir, fname)
    out_meta_json = out_jsonl + ".meta.json"
    return out_jsonl, out_meta_json

# ------------------------------------------------
# Worker
# ------------------------------------------------
def judge_one(
    rec: dict,
    prompt: PromptSpec,
    args,
    client: OpenAI,
    vllm_sema: threading.Semaphore,
    model_profile: dict,
) -> Dict[str, Any]:

    nid = safe_str(rec.get("normalized_id", "")).strip()
    qn = rec.get("question_num", None)
    record_id = build_record_id(nid, qn)

    trial_num = rec.get("trial_num", rec.get("trial_id", None))
    question = safe_str(rec.get("question", ""))
    answer = safe_str(rec.get("chatbot_answer", rec.get("answer", "")))

    ctx_full = safe_str(rec.get("retrieved_context", ""))
    ctx_used = truncate_retrieved_context(ctx_full, args.final_k_used)

    retrieval_meta = rec.get("retrieval_meta", None)
    retrieval_meta_used = None
    if isinstance(retrieval_meta, list) and retrieval_meta:
        retrieval_meta_used = retrieval_meta[: max(1, int(args.final_k_used))]

    messages = build_messages_from_profile(
        profile=model_profile,
        prompt=prompt,
        context=ctx_used,
        question=question,
        answer=answer,
    )

    run_key = build_run_key(
        nid=nid,
        qn=qn,
        prompt_variation=prompt.variation,
        prompt_file=prompt.file_name,
        judge_model=args.model,
        final_k_used=args.final_k_used,
        model_profile=args.model_profile,
    )

    t0 = time.time()
    try:
        with vllm_sema:
            resp = client.chat.completions.create(
                model=args.model,
                messages=messages,
                temperature=args.temperature,
                top_p=args.top_p,
                max_tokens=args.max_tokens,
            )
        text = resp.choices[0].message.content
        score = extract_score(text)
        status = "ok"
        err = None
    except Exception as e:
        text = ""
        score = -1
        status = "error"
        err = f"{e}"

    dt = time.time() - t0

    out = {
        "status": status,
        "record_id": record_id,
        "run_key": run_key,

        "normalized_id": nid,
        "trial_num": trial_num,
        "question_num": qn,

        "question": question,
        "chatbot_answer": answer,
        "human_score": rec.get("human_score", rec.get("Level of factual accuracy", None)),

        "retrieved_context_used": ctx_used,
        "retrieval_meta_used": retrieval_meta_used,

        "stage1_source": {
            "stage1_jsonl": args.stage1_jsonl,
            "stage1_meta_json": args.stage1_meta_json,
            "stage1_final_k": args.stage1_final_k,
            "stage1_condition": args.stage1_condition_label,
        },

        "prompt_variation": prompt.variation,
        "prompt_file": prompt.file_name,
        "prompt_hash": prompt.prompt_hash,

        "judge_model": args.model,
        "model_profile": args.model_profile,
        "vllm_api_base": args.vllm_api_base,
        "generation_config": {
            "temperature": args.temperature,
            "top_p": args.top_p,
            "max_tokens": args.max_tokens,
            "final_k_used": args.final_k_used,
        },

        "ai_reasoning_raw": text,
        "ai_score": score,

        "latency_s": round(dt, 4),
    }

    if err:
        out["error"] = err

    if args.debug_payload:
        user_msg = messages[-1].get("content", "") if messages else ""
        user_preview = user_msg
        if isinstance(user_msg, list):
            user_preview = (user_msg[0].get("text", "") if user_msg else "")
        out["debug"] = {
            "messages_hash": md5_text(json.dumps(messages, ensure_ascii=False)),
            "user_preview": safe_str(user_preview)[:600],
            "ctx_preview": ctx_used[:600],
        }

    return out

# ------------------------------------------------
# CLI + main
# ------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser()

    # Inputs
    p.add_argument("--stage1_jsonl", type=str, required=True)
    p.add_argument("--stage1_meta_json", type=str, default="")
    p.add_argument("--stage1_condition_label", type=str, default="")

    # Evidence truncation
    p.add_argument("--final_k_used", type=int, default=int(os.getenv("FINAL_K_USED", "3")))

    # Prompts
    p.add_argument("--prompt_dirs", nargs="+", default=[])
    p.add_argument("--prompt_set_name", type=str, default=os.getenv("PROMPT_SET_NAME", "accuracy_v1"))
    p.add_argument("--excluded_prompts", nargs="*", default=[])

    # vLLM
    p.add_argument("--vllm_api_base", type=str, default=os.getenv("VLLM_API_BASE", "http://localhost:8000/v1"))
    p.add_argument("--model", type=str, default=os.getenv("VLLM_MODEL_NAME", "qwen3-30b"))

    # Model profile
    p.add_argument("--model_profiles_json", type=str, default=os.getenv("MODEL_PROFILES_JSON", "configs/model_profiles.json"))
    p.add_argument("--model_profile", type=str, default=os.getenv("MODEL_PROFILE", "qwen_instruct_vllm"))

    # Output layout (NEW)
    p.add_argument("--out_root", type=str, default=os.getenv("OUT_ROOT", "."), help="Root folder where LAJ_score_* folders are created.")
    p.add_argument("--model_tag", type=str, default=os.getenv("MODEL_TAG", "QWEN"), help="Short tag used in folder naming, e.g., QWEN or MEDGEMMA.")
    p.add_argument(
        "--out_dir_template",
        type=str,
        default=os.getenv("OUT_DIR_TEMPLATE", "LAJ_score_{model_tag}_RAG_{variation}"),
        help="Folder name template under out_root. Uses {model_tag} and {variation}.",
    )

    # Generation
    p.add_argument("--temperature", type=float, default=float(os.getenv("TEMPERATURE", "0.0")))
    p.add_argument("--top_p", type=float, default=float(os.getenv("TOP_P", "1.0")))
    p.add_argument("--max_tokens", type=int, default=int(os.getenv("MAX_NEW_TOKENS", "768")))

    # Concurrency
    p.add_argument("--vllm_inflight", type=int, default=int(os.getenv("VLLM_INFLIGHT", "8")))
    p.add_argument("--max_workers", type=int, default=int(os.getenv("MAX_WORKERS", "16")))

    # Behavior
    p.add_argument("--force", action="store_true", help="Overwrite each per-prompt output JSONL instead of resuming.")
    p.add_argument("--debug_payload", action="store_true")

    return p.parse_args()

def main():
    args = parse_args()

    # Stage1 meta
    if not args.stage1_meta_json.strip():
        args.stage1_meta_json = infer_stage1_meta_path(args.stage1_jsonl)

    stage1_meta = {}
    if args.stage1_meta_json and os.path.exists(args.stage1_meta_json):
        stage1_meta = load_json(args.stage1_meta_json)

    args.stage1_final_k = stage1_meta.get("final_k", None)

    # Prompt dirs defaults
    if not args.prompt_dirs:
        args.prompt_dirs = [
            "baseline:prompt_comparison_accuracy/baseline",
            "def_only:prompt_comparison_accuracy/prompts_def_only",
            "def_cot:prompt_comparison_accuracy/prompts_cot",
        ]

    prompt_dir_map = parse_prompt_dirs(args.prompt_dirs)
    prompts = load_prompts(prompt_dir_map, excluded=args.excluded_prompts)
    if not prompts:
        raise RuntimeError(f"No prompt JSON files found. Check --prompt_dirs: {args.prompt_dirs}")

    # Model profiles
    profiles = load_model_profiles(args.model_profiles_json)
    if args.model_profile not in profiles:
        raise RuntimeError(f"Unknown model_profile={args.model_profile}. Available: {list(profiles.keys())}")
    model_profile = profiles[args.model_profile]

    # vLLM client
    client = OpenAI(base_url=args.vllm_api_base, api_key="EMPTY")
    client.models.list()
    vllm_sema = threading.Semaphore(max(1, int(args.vllm_inflight)))

    # Load stage1 records once
    stage1_records = list(read_jsonl(args.stage1_jsonl))
    if not stage1_records:
        raise RuntimeError(f"No valid JSON lines found in {args.stage1_jsonl}")

    print("============================================================")
    print("Stage 2 LAJ runner (profile-driven; per-variation folders)")
    print(f"Stage1 JSONL : {args.stage1_jsonl}")
    print(f"Stage1 META  : {args.stage1_meta_json if args.stage1_meta_json else '(none)'}")
    if stage1_meta:
        print(f"Stage1 cond  : retrieval_mode={stage1_meta.get('retrieval_mode')} | scales={stage1_meta.get('allowed_scales')} | stage1_final_k={stage1_meta.get('final_k')}")
        print(f"Stage1 rerank: type={stage1_meta.get('reranker_type')} | model={stage1_meta.get('reranker_model')}")
    print(f"Judge model  : {args.model} @ {args.vllm_api_base}")
    print(f"Model profile: {args.model_profile} -> {model_profile.get('description','')}")
    print(f"Evidence     : final_k_used={args.final_k_used}")
    print(f"Prompts      : {len(prompts)} files across {len(prompt_dir_map)} variations")
    print(f"Concurrency  : max_workers={args.max_workers} | vllm_inflight={args.vllm_inflight} (<= vLLM --max-num-seqs)")
    print(f"Output root  : {args.out_root}")
    print(f"Folder tmpl  : {args.out_dir_template}")
    print("============================================================")

    # Process each prompt file independently => output isolation like your old pipeline
    for prompt in prompts:
        out_jsonl, out_meta_json = resolve_output_paths(
            out_root=args.out_root,
            out_dir_template=args.out_dir_template,
            model_tag=args.model_tag,
            prompt=prompt,
            stage1_jsonl=args.stage1_jsonl,
            judge_model=args.model,
            model_profile=args.model_profile,
            final_k_used=args.final_k_used,
        )

        # Handle overwrite/resume per file
        if args.force and os.path.exists(out_jsonl):
            os.remove(out_jsonl)

        existing_run_keys = set()
        if not args.force:
            existing_run_keys = load_existing_run_keys(out_jsonl)

        # Build tasks for this prompt only
        tasks: List[dict] = []
        for rec in stage1_records:
            nid = safe_str(rec.get("normalized_id", "")).strip()
            qn = rec.get("question_num", None)
            if not nid or qn is None:
                continue

            rk = build_run_key(
                nid, qn, prompt.variation, prompt.file_name, args.model, args.final_k_used, args.model_profile
            )
            if (not args.force) and (rk in existing_run_keys):
                continue
            tasks.append(rec)

        print("------------------------------------------------------------")
        print(f"Prompt: {prompt.variation} / {prompt.file_name}")
        print(f"Output: {out_jsonl}")
        print(f"Tasks : {len(tasks)} (resume skipped: {len(existing_run_keys)})")
        print("------------------------------------------------------------")

        # Write per-prompt run meta
        run_meta = {
            "stage1_jsonl": args.stage1_jsonl,
            "stage1_meta_json": args.stage1_meta_json,
            "stage1_meta": stage1_meta,
            "stage1_condition_label": args.stage1_condition_label,

            "judge_model": args.model,
            "vllm_api_base": args.vllm_api_base,

            "model_profiles_json": args.model_profiles_json,
            "model_profile": args.model_profile,
            "model_profile_resolved": model_profile,

            "prompt_variation": prompt.variation,
            "prompt_file": prompt.file_name,
            "prompt_hash": prompt.prompt_hash,

            "generation_config": {
                "temperature": args.temperature,
                "top_p": args.top_p,
                "max_tokens": args.max_tokens,
                "final_k_used": args.final_k_used,
            },
            "concurrency": {
                "max_workers": args.max_workers,
                "vllm_inflight": args.vllm_inflight,
            },
            "output_jsonl": out_jsonl,
        }
        with open(out_meta_json, "w", encoding="utf-8") as f:
            json.dump(run_meta, f, indent=2, ensure_ascii=False)

        if not tasks:
            continue

        # Execute
        with ThreadPoolExecutor(max_workers=max(1, int(args.max_workers))) as ex:
            futures = [
                ex.submit(judge_one, rec, prompt, args, client, vllm_sema, model_profile)
                for rec in tasks
            ]
            for fut in tqdm(as_completed(futures), total=len(futures), desc=f"stage2_laj:{prompt.variation}:{prompt.file_name}"):
                obj = fut.result()
                append_jsonl(out_jsonl, obj)

        print(f"✅ Done → {out_jsonl}")
        print(f"✅ Meta → {out_meta_json}")

if __name__ == "__main__":
    main()
