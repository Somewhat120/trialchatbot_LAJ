#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
dspy_engine/compile/compile_dspy_copro_skill_openai.py

Option 1: SKILL CONTENT optimization (text-level) using a simple COPRO-like loop:
- Generate N rewritten SKILL.md candidates (language-only rewrites)
- Enforce max_skill_tokens (default 1024 tokens)
- Evaluate each candidate on per-criterion dev JSONL using exact-match accuracy
- Select best candidate (tie-breaker: shorter token length wins)
- Save artifacts to dspy_engine/compiled_artifacts/.../optimizer=copro_skill/...

This avoids DSPy compiled-object pickling entirely and plugs into your existing pipeline
because the output is just a rewritten SKILL text artifact.

RUNTIME METHOD NAME:
- Use method=dspy_opt_prompt when calling the loop runner
- This is reserved for Option 1 (skill content optimization)
- Option 2 uses method=dspy_opt_signature (signature wrapper optimization)

OUTPUT:
- Saves best rewritten SKILL.md as best_skill.md
- Saves all candidates with evaluation metrics in candidates.jsonl
- Plugs into existing loop runner with zero special runtime logic
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

import httpx
from openai import OpenAI, APIError, APIConnectionError, APITimeoutError


# ----------------------------
# Allowed scores per criterion (single source of truth; align with run_dspy_openai_opt_signature)
# ----------------------------
_ALLOWED_SCORES = {
    "safety_c1_critical_harm": {0, 1},
    "safety_c2_individualized_medical_advice": {0, 1},
    "safety_c3_inappropriate_persuasion": {0, 1},
    "reliability_c4_transparency": {1, 5},
    "communication_c6_usefulness": {1, 2, 3, 4, 5},
    "communication_c7_clarity_succinctness": {1, 2, 3, 4, 5},
}


# ----------------------------
# Helpers
# ----------------------------

def now_run_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


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
    """
    SKILL_v1.md -> v1
    SKILL.md -> default
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    m = re.search(r"_v(\d+)$", base)
    return f"v{m.group(1)}" if m else "default"


def artifact_component(value: str) -> str:
    """Normalize a dynamic path component while keeping it human-readable."""
    return str(value).replace(os.sep, "__").replace("/", "__")


def count_tokens(text: str) -> int:
    """
    Deterministic token counting using tiktoken if available.
    Falls back to a conservative heuristic if not installed.
    """
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
# OpenAI calls
# ----------------------------

def load_api_key(path: str) -> str:
    return read_text(path).strip()


def call_openai_json_judge(
    client: OpenAI,
    model: str,
    user_text: str,
    max_output_tokens: int,
    temperature: float,
    top_p: float,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "ok": bool,
        "raw_output": str,
        "final_score": int or -1,
        "reasoning": str,
        "error": str or None
      }
    """
    try:
        kwargs: Dict[str, Any] = {
            "model": model,
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": user_text}]}
            ],
            "text": {"format": {"type": "json_object"}, "verbosity": "medium"},
            "reasoning": {"effort": "low"},
            "store": False,
            "max_output_tokens": max_output_tokens,
        }

        # Mirror your behavior: don't send temperature/top_p for gpt-5 models if unsupported
        if not model.startswith("gpt-5"):
            kwargs["temperature"] = temperature
            kwargs["top_p"] = top_p

        resp = client.responses.create(**kwargs)
        raw = (resp.output_text or "").strip()

        if not raw:
            return {"ok": False, "raw_output": raw, "final_score": -1, "reasoning": "", "error": "Empty output"}

        # Strict JSON parse (best effort fallback to first {...})
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

    except APIError as e:
        # OpenAI API errors (includes 403, 429, 500, etc.)
        error_msg = f"API error {e.status_code}: {str(e)}"
        if e.status_code == 403:
            error_msg = f"API error 403: content filter triggered - {str(e)}"
        return {"ok": False, "raw_output": "", "final_score": -1, "reasoning": "", "error": error_msg}
    except (APIConnectionError, APITimeoutError) as e:
        # Network/timeout errors
        return {"ok": False, "raw_output": "", "final_score": -1, "reasoning": "", "error": f"Connection error: {str(e)}"}
    except Exception as e:
        # Other unexpected errors
        error_str = str(e)
        if "403" in error_str or "forbidden" in error_str.lower():
            return {"ok": False, "raw_output": "", "final_score": -1, "reasoning": "", "error": f"API error 403: content filter triggered - {error_str}"}
        return {"ok": False, "raw_output": "", "final_score": -1, "reasoning": "", "error": f"Unexpected error: {error_str}"}


def call_openai_rewrite_skill(
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
    max_output_tokens: int = 2048,
) -> Dict[str, Any]:
    """
    Returns:
      {"ok": bool, "skill_text": str, "raw_output": str, "error": str or None}
    """
    prior_block = ""
    if prior_candidates_brief:
        prior_block = "\n\nAVOID DUPLICATING THESE PRIOR REWRITES (high-level):\n- " + "\n- ".join(prior_candidates_brief[:5])

    user_prompt = f"""You are rewriting an evaluation rubric (SKILL.md) for an LLM-as-Judge.
Goal: Improve clarity, ordering, and constraint emphasis WITHOUT changing the underlying decision logic.

HARD CONSTRAINTS:
- Preserve the rubric's meaning and decision rules exactly. Do NOT add new rules. Do NOT remove rules.
- Keep any placeholders exactly as-is, especially: {{question}} and {{answer}}.
- Keep the required output JSON schema and keys unchanged (reasoning, final_score).
- Keep score scale unchanged for this criterion.
- Keep headings if present, but you may compress wording.
- The rewritten SKILL must be <= {max_skill_tokens} tokens.

STYLE:
- Plain language.
- Short sentences.
- Reduce redundancy.
- Do not add examples that change scope. If examples exist, you may shorten them.

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
        kwargs: Dict[str, Any] = {
            "model": model,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}],
            "text": {"verbosity": "medium"},
            "reasoning": {"effort": "low"},
            "store": False,
            "max_output_tokens": max_output_tokens,
        }
        if not model.startswith("gpt-5"):
            kwargs["temperature"] = temperature
            kwargs["top_p"] = top_p

        resp = client.responses.create(**kwargs)
        raw = (resp.output_text or "").strip()
        if not raw:
            return {"ok": False, "skill_text": "", "raw_output": raw, "error": "Empty rewrite output"}

        return {"ok": True, "skill_text": raw, "raw_output": raw, "error": None}
    except APIError as e:
        # OpenAI API errors (includes 403, 429, 500, etc.)
        error_msg = f"API error {e.status_code}: {str(e)}"
        if e.status_code == 403:
            error_msg = f"API error 403: content filter triggered - {str(e)}"
        return {"ok": False, "skill_text": "", "raw_output": "", "error": error_msg}
    except (APIConnectionError, APITimeoutError) as e:
        # Network/timeout errors
        return {"ok": False, "skill_text": "", "raw_output": "", "error": f"Connection error: {str(e)}"}
    except Exception as e:
        # Other unexpected errors
        error_str = str(e)
        if "403" in error_str or "forbidden" in error_str.lower():
            return {"ok": False, "skill_text": "", "raw_output": "", "error": f"API error 403: content filter triggered - {error_str}"}
        return {"ok": False, "skill_text": "", "raw_output": "", "error": f"Unexpected error: {error_str}"}


# ----------------------------
# Core evaluation
# ----------------------------

@dataclass
class EvalRow:
    question: str
    answer: str
    gold: int


def build_eval_rows(dev_rows: List[Dict[str, Any]], label_key: str) -> Tuple[List[EvalRow], int]:
    missing = 0
    out: List[EvalRow] = []
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
        out.append(EvalRow(question=q, answer=a, gold=y))
    return out, missing


def eval_skill_on_rows(
    client: OpenAI,
    judge_model: str,
    skill_text: str,
    rows: List[EvalRow],
    max_output_tokens: int,
    temperature: float,
    top_p: float,
    allowed_scores: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Evaluate accuracy by exact match. Errors/invalid outputs count as incorrect.
    """
    hits = 0
    errors = 0
    invalid = 0

    for r in rows:
        user_text = skill_text.replace("{question}", r.question).replace("{answer}", r.answer)
        res = call_openai_json_judge(
            client=client,
            model=judge_model,
            user_text=user_text,
            max_output_tokens=max_output_tokens,
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
    parser = argparse.ArgumentParser(description="Option 1: COPRO-skill (text) optimization for SKILL.md using OpenAI API.")

    parser.add_argument("--criterion_id", required=True)
    parser.add_argument("--skill_md", required=True)
    parser.add_argument("--dev_jsonl", required=True)
    parser.add_argument("--label_key", required=True)

    parser.add_argument("--judge_model", required=True)
    parser.add_argument("--rewriter_model", default=None, help="Defaults to judge_model if omitted.")
    parser.add_argument("--key_file", default="prompt_evaluation_scripts/gpt_key.txt")
    parser.add_argument("--compiled_root", default="dspy_engine/compiled_artifacts")

    parser.add_argument("--depth", type=int, default=1, help="Rewrite rounds. Start with 1.")
    parser.add_argument("--num_candidates", type=int, default=3)
    parser.add_argument("--max_skill_tokens", type=int, default=1024, help="Hard cap for rewritten SKILL size.")
    parser.add_argument("--max_judge_output_tokens", type=int, default=1024, help="Judge output budget (JSON).")

    parser.add_argument("--rewrite_temperature", type=float, default=0.8, help="Higher => more diverse candidate rewrites.")
    parser.add_argument("--rewrite_top_p", type=float, default=1.0)

    parser.add_argument("--judge_temperature", type=float, default=0.0)
    parser.add_argument("--judge_top_p", type=float, default=1.0)

    parser.add_argument("--limit", type=int, default=None, help="Optional dev-row limit for quick testing.")
    parser.add_argument("--max_rewrite_retries", type=int, default=5, help="Retries per candidate to satisfy token/placeholder constraints.")

    args = parser.parse_args()

    if not os.path.isfile(args.skill_md):
        raise FileNotFoundError(f"Missing skill_md: {args.skill_md}")
    if not os.path.isfile(args.dev_jsonl):
        raise FileNotFoundError(f"Missing dev_jsonl: {args.dev_jsonl}")
    if not os.path.isfile(args.key_file):
        raise FileNotFoundError(f"Missing key_file: {args.key_file}")

    rewriter_model = args.rewriter_model or args.judge_model
    backend = "openai"
    run_stamp = now_run_stamp()
    skill_version = extract_version_from_filename(args.skill_md)

    base_skill_text = read_text(args.skill_md)
    base_skill_sha = sha256_text(base_skill_text)
    base_skill_tokens = count_tokens(base_skill_text)

    dev_rows = load_jsonl(args.dev_jsonl)
    if args.limit and args.limit > 0:
        dev_rows = dev_rows[: args.limit]

    eval_rows, missing_label = build_eval_rows(dev_rows, args.label_key)
    if not eval_rows:
        raise ValueError(f"No usable dev examples. label_key='{args.label_key}', rows={len(dev_rows)}, missing_label={missing_label}")

    # Allowed score set (optional strictness) from single map for all criteria
    allowed_scores = _ALLOWED_SCORES.get(args.criterion_id)

    api_key = load_api_key(args.key_file)
    http_client = httpx.Client(verify=False)
    client = OpenAI(api_key=api_key, http_client=http_client)

    # Baseline eval
    t0 = time.time()
    baseline = eval_skill_on_rows(
        client=client,
        judge_model=args.judge_model,
        skill_text=base_skill_text,
        rows=eval_rows,
        max_output_tokens=args.max_judge_output_tokens,
        temperature=args.judge_temperature,
        top_p=args.judge_top_p,
        allowed_scores=allowed_scores,
    )
    baseline_latency_s = time.time() - t0

    # Candidate generation + evaluation
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

    # Depth loop: each round rewrites starting from current "base" (best so far)
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

                rr = call_openai_rewrite_skill(
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
                    max_output_tokens=max(2048, args.max_judge_output_tokens),
                )
                if not rr["ok"]:
                    gen_err = rr["error"]
                    total_rewrite_fail += 1
                    continue

                cand_raw = rr["raw_output"]
                cand_text = rr["skill_text"].strip()

                # Hard validations: placeholders + token cap
                if "{question}" not in cand_text or "{answer}" not in cand_text:
                    gen_err = "Missing required placeholders {question}/{answer}"
                    cand_text = ""
                    continue

                token_count = count_tokens(cand_text)
                if token_count > args.max_skill_tokens:
                    gen_err = f"Candidate exceeds max_skill_tokens: {token_count} > {args.max_skill_tokens}"
                    cand_text = ""
                    continue

                # Passed constraints
                gen_err = None
                break

            if not cand_text:
                # Save invalid candidate record
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

            # Evaluate candidate
            t_eval = time.time()
            stats = eval_skill_on_rows(
                client=client,
                judge_model=args.judge_model,
                skill_text=cand_text,
                rows=eval_rows,
                max_output_tokens=args.max_judge_output_tokens,
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

            # Track tie-breaker A: higher acc, then shorter tokens
            acc = float(stats["accuracy"])
            if (acc > round_best_acc) or (acc == round_best_acc and tok < round_best_tok):
                round_best_acc = acc
                round_best_tok = tok
                round_best_text = cand_text
                round_best_id = cand_id

            # Add brief to encourage diversity (keep short)
            brief = cand_text[:200].replace("\n", " ").strip()
            prior_briefs.append(f"{cand_id}: {brief}")

        # End of round: update base for next depth
        if round_best_text is not None:
            current_base_text = round_best_text

        # Also update global best
        if round_best_text is not None:
            if (round_best_acc > best_acc) or (round_best_acc == best_acc and round_best_tok < best_tok):
                best_acc = round_best_acc
                best_tok = round_best_tok
                best_skill_text = round_best_text
                best_candidate_id = round_best_id

    if not best_skill_text:
        raise RuntimeError(f"No valid candidates produced under max_skill_tokens={args.max_skill_tokens}.")

    # Save best skill
    with open(best_skill_path, "w", encoding="utf-8") as f:
        f.write(best_skill_text.rstrip() + "\n")

    # Meta
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
        "max_judge_output_tokens": args.max_judge_output_tokens,

        "rewrite_temperature": args.rewrite_temperature,
        "rewrite_top_p": args.rewrite_top_p,
        "judge_temperature": args.judge_temperature,
        "judge_top_p": args.judge_top_p,

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

    print("✅ COPRO SKILL CONTENT optimization complete (Option 1)")
    print(f"Artifact dir: {artifact_dir}")
    print(f"Best SKILL:   {best_skill_path}")
    print(f"Candidates:   {candidates_log_path}")
    print(f"Meta:         {meta_path}")
    print(f"Baseline acc: {baseline['accuracy']:.4f}  |  Best acc: {best_acc:.4f}  |  Best tokens: {best_tok}")


if __name__ == "__main__":
    main()
