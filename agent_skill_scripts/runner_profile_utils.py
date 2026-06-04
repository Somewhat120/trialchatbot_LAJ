from __future__ import annotations

import json
from typing import Any, Dict, Tuple


OPENAI_FAMILY_PROFILE = "openai_gpt5_responses"
OPENAI_MODEL_PROFILE_KEYS = (
    "openai_gpt5_mini_responses",
    "openai_gpt5_4_responses",
    "openai_gpt5_2_responses",
)
CLAUDE_PROFILE = "anthropic_claude_messages"
GEMINI_PROFILE = "google_gemini_generate_content"


def load_model_profiles(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        profiles = json.load(handle)
    if not isinstance(profiles, dict) or not profiles:
        raise RuntimeError(f"Model profiles file must be a non-empty object: {path}")
    return profiles


def require_profile(profiles: Dict[str, Any], profile_name: str) -> Dict[str, Any]:
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise RuntimeError(f"Unknown model_profile={profile_name}. Available: {sorted(profiles.keys())}")
    return profile


def resolve_openai_profile_name(judge_model: str, profiles: Dict[str, Any]) -> str:
    model_name = (judge_model or "").strip()
    for profile_name in OPENAI_MODEL_PROFILE_KEYS:
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            continue
        provider = profile.get("publication_metadata", {}).get("provider_api_settings", {})
        model_id = str(provider.get("model_id", "")).strip()
        if model_id and model_name.startswith(model_id):
            return profile_name
    if model_name.startswith("gpt-5"):
        require_profile(profiles, OPENAI_FAMILY_PROFILE)
        return OPENAI_FAMILY_PROFILE
    raise RuntimeError(f"Could not resolve an OpenAI GPT-5 model profile for judge_model={judge_model}")


def build_openai_generation_config(max_output_tokens: int, max_input_chars: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    generation_config = {
        "max_output_tokens": max_output_tokens,
        "json_object": True,
        "verbosity": "low",
        "reasoning_effort": "low",
        "store": False,
        "max_input_chars": max_input_chars,
    }
    openai_cfg = dict(generation_config)
    return generation_config, openai_cfg


def build_claude_generation_config(max_tokens: int) -> Dict[str, Any]:
    return {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
    }


def build_gemini_generation_config(max_output_tokens: int, max_input_chars: int) -> Dict[str, Any]:
    return {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_output_tokens": max_output_tokens,
        "response_mime_type": "application/json",
        "thinking_config": {"thinking_level": "LOW"},
        "max_input_chars": max_input_chars,
    }
