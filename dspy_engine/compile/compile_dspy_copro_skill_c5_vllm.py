#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
dspy_engine/compile/compile_dspy_copro_skill_c5_vllm.py

SKILL CONTENT optimization (text-level) using a simple COPRO-like loop — vLLM backend.
C5 Factual Accuracy specific: Supports {context} placeholder for ICF text.

Forked from compile_dspy_copro_skill_vllm.py; uses vLLM OpenAI-compatible
/v1/chat/completions for both the judge and the rewriter (fully local).

- Generate N rewritten SKILL.md candidates (language-only rewrites)
- Enforce max_skill_tokens (default 1024 tokens)
- Evaluate each candidate on per-criterion dev JSONL using exact-match accuracy
- Select best candidate (tie-breaker: shorter token length wins)
- Save artifacts to dspy_engine/compiled_artifacts/.../backend=vllm/.../optimizer=copro_skill/...

RUNTIME METHOD NAME:
- Use method=dspy_opt_prompt when calling the vLLM loop runner

OUTPUT:
- Saves best rewritten SKILL.md as best_skill.md
- Saves all candidates with evaluation metrics in candidates.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


# ----------------------------
# Allowed scores per criterion (single source of truth)
# ----------------------------
_ALLOWED_SCORES = {
    "safety_c1_critical_harm": {0, 1},
    "safety_c2_individualized_medical_advice": {0, 1},
    "safety_c3_inappropriate_persuasion": {0, 1},
    "reliability_c4_transparency": {1, 5},
    "reliability_c5_factual_accuracy": {1, 2, 3, 4, 5},  # C5-specific
    "communication_c6_usefulness": {1, 2, 3, 4, 5},
    "communication_c7_clarity_succinctness": {1, 2, 3, 4, 5},
}


# ----------------------------
# Helpers
# ----------------------------

def now_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def clean_thinking_tokens(text: str) -> str:
    """
    Remove thinking tokens from MedGemma 1.5 output.
    These tokens like <unused94>thought...<unused95> consume tokens but aren't needed in final output.
    """
    if not text:
        return text
    
    # Remove thinking token blocks: <unused\d+>thought...<unused\d+>
    # This pattern matches the entire thinking section between the tokens
    text = re.sub(r'<unused\d+>thought.*?<unused\d+>', '', text, flags=re.DOTALL)
    
    # Also remove any standalone thinking tokens that might remain
    text = re.sub(r'<unused\d+>', '', text)
    
    # Clean up any extra whitespace
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Multiple newlines -> double newline
    text = text.strip()
    
    return text


def sanitize_skill_artifacts(text: str) -> str:
    """Remove known non-skill artifacts that occasionally leak into rewrites."""
    if not text:
        return text

    text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"\n*Welcome to our community!.*?Happy Rewriting!\s*$",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def extract_version_from_filename(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0]
    m = re.search(r"_v(\d+)$", base)
    return f"v{m.group(1)}" if m else "default"


def artifact_component(value: str) -> str:
    """Normalize a dynamic path component while keeping it human-readable."""
    return str(value).replace(os.sep, "__").replace("/", "__")


def count_tokens(text: str) -> int:
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text))
    except Exception:
        # Rough fallback: ~4 chars/token
        return int(math.ceil(len(text) / 4.0))


def build_artifact_dir(
    root: str,
    criterion_id: str,
    skill_version: str,
    backend: str,
    judge_model: str,
    rewriter_model: str,
    depth: int,
    num_candidates: int,
    run_stamp: str,
) -> str:
    return os.path.join(
        root,
        f"criterion={criterion_id}",
        f"skill_version={skill_version}",
        f"backend={backend}",
        f"judge_model={artifact_component(judge_model)}",
        f"rewriter_model={artifact_component(rewriter_model)}",
        f"optimizer=copro_skill",
        f"depth={depth}",
        f"candidates={num_candidates}",
        f"run={run_stamp}",
    )


# ----------------------------
# Context handling (C5-specific)
# ----------------------------

def get_full_text_by_nct(trial_id: str, text_folder: str) -> Optional[str]:
    """
    Load full ICF text from external files by trial_num (NCT ID).
    Returns None if file not found.
    """
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


def get_context_for_row(row: dict, text_folder: Optional[str]) -> str:
    """
    Get context for a row with priority:
    1. evidence_snippet from JSONL (if present)
    2. Full text from text_folder (if provided and file exists)
    3. Fallback message
    
    Returns the context string to use.
    """
    # Priority 1: evidence_snippet from JSONL
    evidence_snippet = row.get("evidence_snippet")
    if evidence_snippet:
        return str(evidence_snippet).strip()
    
    # Priority 2: Full text from external files
    if text_folder:
        trial_num = row.get("trial_num")
        if trial_num:
            full_text = get_full_text_by_nct(trial_num, text_folder)
            if full_text:
                return full_text.strip()
    
    # Priority 3: Fallback message
    return (
        "NOTE: No ICF (Informed Consent Form) text is available for this trial. "
        "Please evaluate factual accuracy using standard general knowledge and clinical trial principles."
    )


# ----------------------------
# vLLM chat completions calls
# ----------------------------

def call_vllm_chat_judge(
    client: OpenAI,
    model: str,
    user_text: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> Dict[str, Any]:
    """Judge via vLLM chat completions. No JSON-mode enforcement."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": user_text}],
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        raw = (resp.choices[0].message.content or "").strip()

        if not raw:
            return {"ok": False, "raw_output": raw, "final_score": -1, "reasoning": "", "error": "Empty output"}

        obj = None
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception as e2:
                    return {"ok": False, "raw_output": raw, "final_score": -1, "reasoning": "", "error": f"JSON parse failed: {e2}"}
            else:
                return {"ok": False, "raw_output": raw, "final_score": -1, "reasoning": "", "error": "No JSON object found"}

        reasoning = str(obj.get("reasoning", "") if isinstance(obj, dict) else "")
        score_raw = obj.get("final_score", None) if isinstance(obj, dict) else None
        try:
            score = int(score_raw)
        except Exception:
            score = -1

        return {"ok": True, "raw_output": raw, "final_score": score, "reasoning": reasoning, "error": None}

    except Exception as e:
        return {"ok": False, "raw_output": "", "final_score": -1, "reasoning": "", "error": f"vLLM error: {e}"}


def call_vllm_chat_rewrite(
    client: OpenAI,
    model: str,
    base_skill_text: str,
    criterion_id: str,
    max_skill_tokens: int,
    temperature: float,
    top_p: float,
    depth_step: int,
    candidate_idx: int,
    prior_candidates_brief: Optional[List[str]] = None,
    max_tokens: int = 2048,
    has_context_placeholder: bool = False,
) -> Dict[str, Any]:
    """Rewrite via vLLM chat completions. C5-specific: preserves {context} placeholder."""
    prior_block = ""
    if prior_candidates_brief:
        prior_block = "\n\nAVOID DUPLICATING THESE PRIOR REWRITES (high-level):\n- " + "\n- ".join(prior_candidates_brief[:5])

    # C5-specific: Include {context} in placeholder preservation if present
    placeholder_note = "{question} and {answer}"
    if has_context_placeholder:
        placeholder_note = "{context}, {question}, and {answer}"

    user_prompt = f"""You are rewriting an evaluation rubric (SKILL.md) for an LLM-as-Judge.
Goal: Improve clarity, ordering, and constraint emphasis WITHOUT changing the underlying decision logic.

HARD CONSTRAINTS:
- Preserve the rubric's meaning and decision rules exactly. Do NOT add new rules. Do NOT remove rules.
- Keep any placeholders exactly as-is, especially: {placeholder_note}.
- Keep the required output JSON schema and keys unchanged (reasoning, final_score).
- Keep score scale unchanged for this criterion.
- Keep headings if present, but you may compress wording.
- The rewritten SKILL must be <= {max_skill_tokens} tokens.

STYLE:
- Plain language.
- Short sentences.
- Reduce redundancy.
- Do not add examples that change scope. If examples exist, you may shorten them.
- Be aggressively concise. Prefer compact bullets over long prose.
- Target substantially below the hard limit, ideally 60-85% of the token budget.
- Delete repeated framing, repeated cautions, and repeated restatements of the same rule.

UNIQUENESS:
- Produce a rewrite that is meaningfully different in wording/structure from other candidates.

CONTEXT:
- criterion_id: {criterion_id}
- depth step: {depth_step}
- candidate: {candidate_idx}
{prior_block}

RETURN ONLY THE FULL REWRITTEN SKILL TEXT. No markdown fences.

=== BASE SKILL START ===
{base_skill_text}
=== BASE SKILL END ===
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if not raw:
            return {"ok": False, "skill_text": "", "raw_output": raw, "error": "Empty rewrite output"}

        # Extract actual skill text by removing thinking tokens
        skill_text = sanitize_skill_artifacts(clean_thinking_tokens(raw))
        if not skill_text:
            return {"ok": False, "skill_text": "", "raw_output": raw, "error": "Empty skill text after cleaning thinking tokens"}

        return {"ok": True, "skill_text": skill_text, "raw_output": raw, "error": None}
    except Exception as e:
        return {"ok": False, "skill_text": "", "raw_output": "", "error": f"vLLM error: {e}"}


# ----------------------------
# Core evaluation
# ----------------------------

@dataclass
class EvalRow:
    question: str
    answer: str
    context: str  # C5-specific: ICF context
    gold: int


def build_eval_rows(
    dev_rows: List[Dict[str, Any]], 
    label_key: str,
    text_folder: Optional[str] = None,
) -> Tuple[List[EvalRow], int, Dict[str, int]]:
    """
    Build evaluation rows with context support.
    Returns: (eval_rows, missing_label_count, context_stats)
    """
    missing = 0
    out: List[EvalRow] = []
    context_stats = {
        "evidence_snippet": 0,
        "full_text": 0,
        "fallback": 0,
    }
    
    for r in dev_rows:
        if label_key not in r:
            missing += 1
            continue
        q = str(r.get("question", "")).strip()
        a = str(r.get("answer", "")).strip()
        try:
            y = int(r.get(label_key))
        except Exception:
            continue
        
        # Get context with priority tracking
        context = get_context_for_row(r, text_folder)
        
        # Track context source for statistics
        if r.get("evidence_snippet"):
            context_stats["evidence_snippet"] += 1
        elif text_folder and r.get("trial_num") and get_full_text_by_nct(r.get("trial_num"), text_folder):
            context_stats["full_text"] += 1
        else:
            context_stats["fallback"] += 1
        
        out.append(EvalRow(question=q, answer=a, context=context, gold=y))
    
    return out, missing, context_stats


def eval_skill_on_rows(
    client: OpenAI,
    judge_model: str,
    skill_text: str,
    rows: List[EvalRow],
    max_tokens: int,
    temperature: float,
    top_p: float,
    allowed_scores: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Evaluate accuracy by exact match. Errors/invalid outputs count as incorrect.
    C5-specific: Replaces {context}, {question}, {answer} placeholders.
    """
    hits = 0
    errors = 0
    invalid = 0

    for r in rows:
        # Replace all placeholders (C5-specific: includes {context})
        user_text = skill_text.replace("{context}", r.context) \
                              .replace("{question}", r.question) \
                              .replace("{answer}", r.answer)
        
        res = call_vllm_chat_judge(
            client=client,
            model=judge_model,
            user_text=user_text,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        if not res["ok"]:
            errors += 1
            continue

        pred = res["final_score"]
        if allowed_scores is not None and pred not in allowed_scores:
            invalid += 1
            continue

        if pred == r.gold:
            hits += 1

    total = len(rows)
    acc = hits / total if total else 0.0
    return {
        "accuracy": acc,
        "hits": hits,
        "total": total,
        "errors": errors,
        "invalid": invalid,
    }


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="COPRO-skill (text) optimization for SKILL.md using vLLM (OpenAI-compatible chat completions). C5 Factual Accuracy specific."
    )

    parser.add_argument("--criterion_id", required=True)
    parser.add_argument("--skill_md", required=True)
    parser.add_argument("--dev_jsonl", required=True)
    parser.add_argument("--label_key", required=True)

    parser.add_argument("--judge_model", required=True, help="Served model name on vLLM")
    parser.add_argument("--rewriter_model", default=None, help="Defaults to judge_model if omitted.")
    parser.add_argument("--vllm_api_base", type=str, default="http://localhost:8000/v1", help="vLLM base URL")
    parser.add_argument("--compiled_root", default="dspy_engine/compiled_artifacts")

    parser.add_argument("--text_folder", type=str, default=None, help="Optional: Folder containing NCT*.txt ICF full text files. For vLLM, evidence_snippet from JSONL is primary; full text is optional enhancement.")

    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--num_candidates", type=int, default=3)
    parser.add_argument("--max_skill_tokens", type=int, default=1024)
    parser.add_argument("--max_judge_output_tokens", type=int, default=1024)

    parser.add_argument("--rewrite_temperature", type=float, default=0.8)
    parser.add_argument("--rewrite_top_p", type=float, default=1.0)

    parser.add_argument("--judge_temperature", type=float, default=0.0)
    parser.add_argument("--judge_top_p", type=float, default=1.0)

    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max_rewrite_retries", type=int, default=5)

    args = parser.parse_args()

    if not os.path.isfile(args.skill_md):
        raise FileNotFoundError(f"Missing skill_md: {args.skill_md}")
    if not os.path.isfile(args.dev_jsonl):
        raise FileNotFoundError(f"Missing dev_jsonl: {args.dev_jsonl}")

    rewriter_model = args.rewriter_model or args.judge_model
    backend = "vllm"
    run_stamp = now_run_stamp()
    skill_version = extract_version_from_filename(args.skill_md)

    base_skill_text = read_text(args.skill_md)
    base_skill_sha = sha256_text(base_skill_text)
    base_skill_tokens = count_tokens(base_skill_text)
    
    # Check if original SKILL has {context} placeholder
    has_context_placeholder = "{context}" in base_skill_text

    dev_rows = load_jsonl(args.dev_jsonl)
    if args.limit and args.limit > 0:
        dev_rows = dev_rows[: args.limit]

    eval_rows, missing_label, context_stats = build_eval_rows(dev_rows, args.label_key, args.text_folder)
    if not eval_rows:
        raise ValueError(f"No usable dev examples. label_key='{args.label_key}', rows={len(dev_rows)}, missing_label={missing_label}")

    print(f"Context stats: evidence_snippet={context_stats['evidence_snippet']}, "
          f"full_text={context_stats['full_text']}, fallback={context_stats['fallback']}")

    allowed_scores = _ALLOWED_SCORES.get(args.criterion_id)

    client = OpenAI(base_url=args.vllm_api_base, api_key="EMPTY")

    try:
        models = client.models.list()
        served = {m.id for m in models.data}
        print(f"Connected to vLLM: {args.vllm_api_base}")
        print(f"Served models: {sorted(served)}")
        if args.judge_model not in served:
            raise RuntimeError(f"judge_model='{args.judge_model}' not in served models: {sorted(served)}")
        if rewriter_model not in served:
            raise RuntimeError(f"rewriter_model='{rewriter_model}' not in served models: {sorted(served)}")
    except Exception as e:
        if "not in served" in str(e) or "not found" in str(e):
            raise
        raise RuntimeError(f"Failed to connect to vLLM server at {args.vllm_api_base}: {e}")

    t0 = time.time()
    baseline = eval_skill_on_rows(
        client=client,
        judge_model=args.judge_model,
        skill_text=base_skill_text,
        rows=eval_rows,
        max_tokens=args.max_judge_output_tokens,
        temperature=args.judge_temperature,
        top_p=args.judge_top_p,
        allowed_scores=allowed_scores,
    )
    baseline_latency_s = time.time() - t0

    candidates_log_path = None
    best_skill_text = None
    best_acc = -1.0
    best_tok = 10**9
    best_candidate_id = None

    artifact_dir = build_artifact_dir(
        root=args.compiled_root,
        criterion_id=args.criterion_id,
        skill_version=skill_version,
        backend=backend,
        judge_model=args.judge_model,
        rewriter_model=rewriter_model,
        depth=args.depth,
        num_candidates=args.num_candidates,
        run_stamp=run_stamp,
    )
    ensure_dir(artifact_dir)

    candidates_log_path = os.path.join(artifact_dir, "candidates.jsonl")
    best_skill_path = os.path.join(artifact_dir, "best_skill.md")
    meta_path = os.path.join(artifact_dir, "compile_meta.json")

    prior_briefs: List[str] = []
    total_rewrite_calls = 0
    total_rewrite_fail = 0

    current_base_text = base_skill_text

    for d in range(1, args.depth + 1):
        round_best_text = None
        round_best_acc = -1.0
        round_best_tok = 10**9
        round_best_id = None

        for i in range(1, args.num_candidates + 1):
            cand_id = f"d{d}_c{i}"
            cand_text = ""
            cand_raw = ""
            gen_err = None
            retries = 0
            token_count = None

            while retries < args.max_rewrite_retries:
                retries += 1
                total_rewrite_calls += 1

                rr = call_vllm_chat_rewrite(
                    client=client,
                    model=rewriter_model,
                    base_skill_text=current_base_text,
                    criterion_id=args.criterion_id,
                    max_skill_tokens=args.max_skill_tokens,
                    temperature=args.rewrite_temperature,
                    top_p=args.rewrite_top_p,
                    depth_step=d,
                    candidate_idx=i,
                    prior_candidates_brief=prior_briefs,
                    max_tokens=max(4096, args.max_judge_output_tokens * 2),  # Increased to accommodate thinking tokens
                    has_context_placeholder=has_context_placeholder,
                )
                if not rr["ok"]:
                    gen_err = rr["error"]
                    total_rewrite_fail += 1
                    continue

                cand_raw = rr["raw_output"]
                cand_text = rr["skill_text"].strip()

                # Validate required placeholders
                if "{question}" not in cand_text or "{answer}" not in cand_text:
                    gen_err = "Missing required placeholders {question}/{answer}"
                    cand_text = ""
                    continue
                
                # C5-specific: Validate {context} if present in original
                if has_context_placeholder and "{context}" not in cand_text:
                    gen_err = "Missing required placeholder {context}"
                    cand_text = ""
                    continue

                token_count = count_tokens(cand_text)
                if token_count > args.max_skill_tokens:
                    gen_err = f"Candidate exceeds max_skill_tokens: {token_count} > {args.max_skill_tokens}"
                    cand_text = ""
                    continue

                gen_err = None
                break

            if not cand_text:
                rec = {
                    "candidate_id": cand_id,
                    "status": "invalid",
                    "reason": gen_err or "Unknown rewrite failure",
                    "rewrite_retries": retries,
                    "skill_sha256": sha256_text(cand_raw or ""),
                    "skill_tokens": token_count,
                    "accuracy": None,
                }
                with open(candidates_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                continue

            t_eval = time.time()
            stats = eval_skill_on_rows(
                client=client,
                judge_model=args.judge_model,
                skill_text=cand_text,
                rows=eval_rows,
                max_tokens=args.max_judge_output_tokens,
                temperature=args.judge_temperature,
                top_p=args.judge_top_p,
                allowed_scores=allowed_scores,
            )
            eval_latency = time.time() - t_eval

            tok = token_count if token_count is not None else count_tokens(cand_text)

            rec = {
                "candidate_id": cand_id,
                "status": "ok",
                "rewrite_retries": retries,
                "skill_sha256": sha256_text(cand_text),
                "skill_tokens": tok,
                "skill_chars": len(cand_text),
                "accuracy": stats["accuracy"],
                "hits": stats["hits"],
                "total": stats["total"],
                "errors": stats["errors"],
                "invalid": stats["invalid"],
                "eval_latency_s": eval_latency,
            }
            with open(candidates_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            acc = float(stats["accuracy"])
            if (acc > round_best_acc) or (acc == round_best_acc and tok < round_best_tok):
                round_best_acc = acc
                round_best_tok = tok
                round_best_text = cand_text
                round_best_id = cand_id

            brief = cand_text[:200].replace("\n", " ").strip()
            prior_briefs.append(f"{cand_id}: {brief}")

        if round_best_text is not None:
            current_base_text = round_best_text

        if round_best_text is not None:
            if (round_best_acc > best_acc) or (round_best_acc == best_acc and round_best_tok < best_tok):
                best_acc = round_best_acc
                best_tok = round_best_tok
                best_skill_text = round_best_text
                best_candidate_id = round_best_id

    if not best_skill_text:
        failure_reason = f"No valid candidates produced under max_skill_tokens={args.max_skill_tokens}."
        meta = {
            "status": "failed",
            "compiled_at": run_stamp,
            "backend": backend,
            "criterion_id": args.criterion_id,
            "optimizer": "COPRO_SKILL",
            "optimization_type": "skill_content",
            "depth": args.depth,
            "num_candidates": args.num_candidates,
            "max_skill_tokens": args.max_skill_tokens,
            "skill_md": args.skill_md,
            "skill_version": skill_version,
            "skill_text_sha256": base_skill_sha,
            "skill_file_sha256": sha256_file(args.skill_md),
            "base_skill_tokens": base_skill_tokens,
            "dev_jsonl": args.dev_jsonl,
            "dev_file_sha256": sha256_file(args.dev_jsonl),
            "label_key": args.label_key,
            "num_rows_loaded": len(dev_rows),
            "num_examples_used": len(eval_rows),
            "num_missing_label": missing_label,
            "judge_model": args.judge_model,
            "rewriter_model": rewriter_model,
            "vllm_api_base": args.vllm_api_base,
            "max_judge_output_tokens": args.max_judge_output_tokens,
            "rewrite_temperature": args.rewrite_temperature,
            "rewrite_top_p": args.rewrite_top_p,
            "judge_temperature": args.judge_temperature,
            "judge_top_p": args.judge_top_p,
            "text_folder": args.text_folder,
            "context_stats": context_stats,
            "has_context_placeholder": has_context_placeholder,
            "baseline_acc_on_compile_set": baseline["accuracy"],
            "baseline_hits": baseline["hits"],
            "baseline_total": baseline["total"],
            "baseline_errors": baseline["errors"],
            "baseline_invalid": baseline["invalid"],
            "baseline_eval_latency_s": baseline_latency_s,
            "artifact_dir": artifact_dir,
            "best_skill_path": best_skill_path,
            "candidates_log_path": candidates_log_path,
            "rewrite_calls_total": total_rewrite_calls,
            "rewrite_calls_failed": total_rewrite_fail,
            "failure_reason": failure_reason,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        raise RuntimeError(failure_reason)

    with open(best_skill_path, "w", encoding="utf-8") as f:
        f.write(best_skill_text.rstrip() + "\n")

    # Determine context source for metadata (primary source used)
    context_source = "evidence_snippet"
    if context_stats["full_text"] > context_stats["evidence_snippet"]:
        context_source = "full_text"
    elif context_stats["fallback"] > context_stats["evidence_snippet"]:
        context_source = "fallback"

    meta = {
        "status": "ok",
        "compiled_at": run_stamp,
        "backend": backend,
        "criterion_id": args.criterion_id,
        "optimizer": "COPRO_SKILL",
        "optimization_type": "skill_content",
        "depth": args.depth,
        "num_candidates": args.num_candidates,
        "tie_breaker": "A_shorter_tokens_wins",
        "max_skill_tokens": args.max_skill_tokens,
        "skill_md": args.skill_md,
        "skill_version": skill_version,
        "skill_text_sha256": base_skill_sha,
        "skill_file_sha256": sha256_file(args.skill_md),
        "base_skill_tokens": base_skill_tokens,
        "dev_jsonl": args.dev_jsonl,
        "dev_file_sha256": sha256_file(args.dev_jsonl),
        "label_key": args.label_key,
        "num_rows_loaded": len(dev_rows),
        "num_examples_used": len(eval_rows),
        "num_missing_label": missing_label,
        "judge_model": args.judge_model,
        "rewriter_model": rewriter_model,
        "vllm_api_base": args.vllm_api_base,
        "max_judge_output_tokens": args.max_judge_output_tokens,
        "rewrite_temperature": args.rewrite_temperature,
        "rewrite_top_p": args.rewrite_top_p,
        "judge_temperature": args.judge_temperature,
        "judge_top_p": args.judge_top_p,
        "text_folder": args.text_folder,
        "context_source": context_source,
        "context_stats": context_stats,
        "has_context_placeholder": has_context_placeholder,
        "baseline_acc_on_compile_set": baseline["accuracy"],
        "baseline_hits": baseline["hits"],
        "baseline_total": baseline["total"],
        "baseline_errors": baseline["errors"],
        "baseline_invalid": baseline["invalid"],
        "baseline_eval_latency_s": baseline_latency_s,
        "best_candidate_id": best_candidate_id,
        "best_acc_on_compile_set": best_acc,
        "best_skill_sha256": sha256_text(best_skill_text),
        "best_skill_tokens": best_tok,
        "artifact_dir": artifact_dir,
        "best_skill_path": best_skill_path,
        "candidates_log_path": candidates_log_path,
        "rewrite_calls_total": total_rewrite_calls,
        "rewrite_calls_failed": total_rewrite_fail,
    }

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("✅ COPRO SKILL CONTENT optimization complete (vLLM backend, C5 Factual Accuracy)")
    print(f"Artifact dir: {artifact_dir}")
    print(f"Best SKILL:   {best_skill_path}")
    print(f"Candidates:   {candidates_log_path}")
    print(f"Meta:         {meta_path}")
    print(f"Baseline acc: {baseline['accuracy']:.4f}  |  Best acc: {best_acc:.4f}  |  Best tokens: {best_tok}")
    print(f"Context stats: evidence_snippet={context_stats['evidence_snippet']}, "
          f"full_text={context_stats['full_text']}, fallback={context_stats['fallback']}")


if __name__ == "__main__":
    main()
