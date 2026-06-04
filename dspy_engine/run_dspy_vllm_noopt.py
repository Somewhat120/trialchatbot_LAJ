# dspy_engine/run_dspy_vllm_noopt.py
# DSPy-noopt caller for vLLM (OpenAI-compatible chat/completions)

from __future__ import annotations

import json
import time
from typing import Any, Dict, Type

import dspy

from dspy_engine.signatures import Binary01Signature, Endpoints15Signature, Likert15Signature
from dspy_engine.lm_vllm_chat import VLLMChatLM


_ALLOWED_SCORES = {
    "safety_c1_critical_harm": {0, 1},
    "safety_c2_individualized_medical_advice": {0, 1},
    "safety_c3_inappropriate_persuasion": {0, 1},
    "reliability_c4_transparency": {1, 5},
    "communication_c6_usefulness": {1, 2, 3, 4, 5},
    "communication_c7_clarity_succinctness": {1, 2, 3, 4, 5},
}


def _pick_signature(criterion_id: str) -> Type[dspy.Signature]:
    if criterion_id in (
        "safety_c1_critical_harm",
        "safety_c2_individualized_medical_advice",
        "safety_c3_inappropriate_persuasion",
    ):
        return Binary01Signature
    if criterion_id == "reliability_c4_transparency":
        return Endpoints15Signature
    return Likert15Signature


def _safe_int(x: Any) -> int:
    try:
        return int(x)
    except Exception:
        return -1


def _validate_score(criterion_id: str, score: int) -> str | None:
    allowed = _ALLOWED_SCORES.get(criterion_id)
    if allowed and score not in allowed:
        return f"Score {score} not in allowed set {sorted(list(allowed))}."
    return None


def call_dspy_vllm_noopt(
    lm: VLLMChatLM,
    skill_text: str,
    question: str,
    answer: str,
    criterion_id: str,
    context: str | None = None,
) -> Dict[str, Any]:
    """
    Mirrors call_openai()/call_vllm_chat() return shape used by your runners.
    
    C5-specific: If context is provided and skill_text contains {context} placeholder,
    replace it before passing to DSPy signature.
    """
    t0 = time.time()
    try:
        dspy.settings.configure(lm=lm)

        # C5-specific: Replace {context} placeholder if present
        if context is not None and "{context}" in skill_text:
            skill_text = skill_text.replace("{context}", context)

        Sig = _pick_signature(criterion_id)
        pred = dspy.Predict(Sig)

        out = pred(skill_text=skill_text, question=question, answer=answer)

        reasoning = getattr(out, "reasoning", "")
        score = _safe_int(getattr(out, "final_score", -1))

        parse_error = _validate_score(criterion_id, score)
        if parse_error:
            score = -1

        raw = json.dumps({"reasoning": reasoning, "final_score": score}, ensure_ascii=False)

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
            "raw_output": getattr(lm, "last_raw_text", ""),
            "parse_error": None,
            "error": str(e),
            "latency_s": time.time() - t0,
        }
