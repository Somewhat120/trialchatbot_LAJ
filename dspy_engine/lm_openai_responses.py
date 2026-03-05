# dspy_engine/lm_openai_responses.py
# OpenAI Responses API -> DSPy LM adapter (DSPy 3.x)

from __future__ import annotations

import json
import time
from typing import Any, List, Optional, Union

import dspy
from openai import OpenAI, APIError, APIConnectionError, APITimeoutError


class OpenAIResponsesLM(dspy.LM):
    """
    Minimal DSPy LM adapter that calls OpenAI Responses API.

    Notes:
    - We pass a single user prompt (string). DSPy provides the prompt.
    - We request JSON object formatting. The signature also instructs JSON-only.
    - GPT-5 family does not support temperature/top_p; we conditionally omit them.
    """

    def __init__(
        self,
        client: OpenAI,
        model: str,
        max_output_tokens: int = 768,
        temperature: float = 0.0,
        top_p: float = 1.0,
        json_object: bool = True,
        verbosity: str = "medium",
        reasoning_effort: str = "low",
        store: bool = False,
    ):
        super().__init__(model=model)
        self.client = client
        self.model = model

        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.top_p = top_p

        self.json_object = json_object
        self.verbosity = verbosity
        self.reasoning_effort = reasoning_effort
        self.store = store

        # last-call metadata (useful for logging)
        self.last_latency_s: Optional[float] = None
        self.last_raw_text: str = ""

    @staticmethod
    def _messages_to_text(messages: Any) -> str:
        """
        Best-effort conversion of DSPy-style messages to a single prompt string.
        Handles:
          - list[str]
          - list[dict] with 'content' and/or 'role'
        """
        if messages is None:
            return ""

        if isinstance(messages, str):
            return messages

        if isinstance(messages, list):
            parts = []
            for m in messages:
                if isinstance(m, str):
                    parts.append(m)
                elif isinstance(m, dict):
                    role = m.get("role", "")
                    content = m.get("content", "")
                    # content could itself be list of blocks or a string
                    if isinstance(content, list):
                        # try to extract text fields
                        txts = []
                        for blk in content:
                            if isinstance(blk, dict) and "text" in blk:
                                txts.append(str(blk["text"]))
                            else:
                                txts.append(str(blk))
                        content_str = "\n".join(txts)
                    else:
                        content_str = str(content)
                    if role:
                        parts.append(f"{role}: {content_str}")
                    else:
                        parts.append(content_str)
                else:
                    parts.append(str(m))
            return "\n".join(parts)

        return str(messages)

    def __call__(
        self,
        prompt: Optional[str] = None,
        **kwargs: Any
    ) -> List[str]:
        """
        DSPy may call LM with:
          - __call__(prompt="...") OR __call__("...")
          - OR __call__(messages=[...])
        We support both.
        """
        t0 = time.time()

        # Prefer explicit prompt; otherwise try kwargs variants
        if prompt is None:
            prompt = kwargs.get("prompt", None)

        if prompt is None:
            prompt = self._messages_to_text(kwargs.get("messages", None))

        if prompt is None:
            prompt = ""

        req: dict[str, Any] = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "text": {
                "format": {"type": "json_object"} if self.json_object else None,
                "verbosity": self.verbosity,
            },
            "reasoning": {"effort": self.reasoning_effort},
            "store": self.store,
            "max_output_tokens": self.max_output_tokens,
        }

        # GPT-5* models do NOT support temperature / top_p
        if not str(self.model).startswith("gpt-5"):
            req["temperature"] = self.temperature
            req["top_p"] = self.top_p

        try:
            resp = self.client.responses.create(**req)
            raw = resp.output_text or ""
        except APIError as e:
            # OpenAI API errors (includes 403, 429, 500, etc.)
            if e.status_code == 403:
                # 403 = content filter/policy violation - return deterministic failure for COPRO
                raw = json.dumps({"reasoning": "API error 403: content filter triggered", "final_score": -1}, ensure_ascii=False)
            else:
                raw = json.dumps({"reasoning": f"API error {e.status_code}: {str(e)}", "final_score": -1}, ensure_ascii=False)
        except (APIConnectionError, APITimeoutError) as e:
            # Network/timeout errors
            raw = json.dumps({"reasoning": f"Connection error: {str(e)}", "final_score": -1}, ensure_ascii=False)
        except Exception as e:
            # Other unexpected errors
            error_str = str(e)
            # Check for 403 in error message as fallback
            if "403" in error_str or "forbidden" in error_str.lower():
                raw = json.dumps({"reasoning": "API error 403: content filter triggered", "final_score": -1}, ensure_ascii=False)
            else:
                raw = json.dumps({"reasoning": f"Unexpected error: {error_str}", "final_score": -1}, ensure_ascii=False)

        self.last_latency_s = time.time() - t0
        self.last_raw_text = raw

        return [raw]
