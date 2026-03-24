# dspy_engine/run_dspy_anthropic_noopt.py
# DSPy (no optimization) judge caller for Anthropic Messages API.
# Drop-in compatible with agent runner: returns same dict shape as call_anthropic().

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional, Tuple, Type

import dspy

from dspy_engine.signatures import Binary01Signature, Endpoints15Signature, Likert15Signature
from dspy_engine.lm_anthropic import AnthropicMessagesLM


# Map criterion_id -> allowed score set
_ALLOWED_SCORES = {
    "safety_c1_critical_harm": {0, 1},
    "safety_c2_individualized_medical_advice": {0, 1},
    "safety_c3_inappropriate_persuasion": {0, 1},
    "reliability_c4_transparency": {1, 5},
    "communication_c6_usefulness": {1, 2, 3, 4, 5},
    "communication_c7_clarity_succinctness": {1, 2, 3, 4, 5},
}


def _pick_signature(criterion_id: str) -> Type[dspy.Signature]:
    """
    Select DSPy signature based on score scale.
    Criterion semantics remain in SKILL.md (skill_text).
    """
    if criterion_id in (
        "safety_c1_critical_harm",
        "safety_c2_individualized_medical_advice",
        "safety_c3_inappropriate_persuasion",
    ):
        return Binary01Signature
    if criterion_id == "reliability_c4_transparency":
        return Endpoints15Signature
    return Likert15Signature  # default Likert 1-5


def _parse_json_output(raw: str) -> Tuple[str, int, Optional[str]]:
    """
    Return (reasoning, score, parse_error).
    Accept strict JSON; fallback: extract first {...} block.
    """
    s = (raw or "").strip()
    if not s:
        return "", -1, "Empty output"

    # Strip markdown code fences if present (Claude sometimes wraps JSON)
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)

    # Try strict parse
    try:
        obj = json.loads(s)
    except Exception:
        # Fallback: extract first JSON object-looking block
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            return "", -1, "No JSON object found"
        try:
            obj = json.loads(m.group(0))
        except Exception as e2:
            return "", -1, f"JSON parse failed: {e2}"

    reasoning = obj.get("reasoning", "")
    score_raw = obj.get("final_score", None)

    try:
        score = int(score_raw)
    except Exception:
        return str(reasoning), -1, "final_score is not an int"

    return str(reasoning), score, None


def call_dspy_anthropic_noopt(
    lm: AnthropicMessagesLM,
    skill_text: str,
    question: str,
    answer: str,
    criterion_id: str,
) -> Dict[str, Any]:
    """
    Anthropic-only DSPy noopt call.

    Returns dict with keys aligned to your runner's expectations:
      - ai_reasoning
      - ai_score
      - raw_output
      - parse_error (optional)
      - error (optional)
      - latency_s
    """
    t0 = time.time()

    try:
        Sig = _pick_signature(criterion_id)

        # Configure DSPy with this LM for the current process.
        # Safe to call repeatedly; DSPy stores it globally.
        dspy.settings.configure(lm=lm)

        pred = dspy.Predict(Sig)

        # NOTE: skill_text should be the raw SKILL.md contents (including your fixed demos),
        # NOT the post-replaced user_text. We pass question/answer separately.
        out = pred(skill_text=skill_text, question=question, answer=answer)

        # DSPy parsed outputs directly into fields
        reasoning = getattr(out, "reasoning", "") or ""
        score = getattr(out, "final_score", None)

        # Build a raw JSON string for logging parity
        raw = json.dumps({"reasoning": reasoning, "final_score": score}, ensure_ascii=False)

        # Validate and convert score
        parse_error = None
        try:
            score = int(score) if score is not None else -1
        except (ValueError, TypeError):
            parse_error = "final_score is not an int"
            score = -1

        # Validate allowed score range for this criterion
        allowed = _ALLOWED_SCORES.get(criterion_id)
        if allowed is not None and score not in allowed:
            parse_error = (parse_error or "") + f" Score {score} not in allowed set {sorted(list(allowed))}."
            score = -1

        # Prefer adapter-reported latency, else compute
        latency = getattr(lm, "last_latency_s", None)
        if latency is None:
            latency = time.time() - t0

        return {
            "ai_reasoning": reasoning,
            "ai_score": score,
            "raw_output": raw,
            "parse_error": parse_error,
            "error": None,
            "latency_s": latency,
        }

    except Exception as e:
        return {
            "ai_reasoning": "",
            "ai_score": -1,
            "raw_output": getattr(lm, "last_raw_text", "") if lm else "",
            "parse_error": None,
            "error": str(e),
            "latency_s": time.time() - t0,
        }
