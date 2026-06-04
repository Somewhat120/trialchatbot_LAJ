# dspy_engine/lm_anthropic.py
# Anthropic Messages API -> DSPy LM adapter (DSPy 3.x)

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Union

import dspy
import anthropic


class AnthropicMessagesLM(dspy.LM):
    """
    Minimal DSPy LM adapter that calls Anthropic Messages API.

    Notes:
    - We pass a single user prompt (string). DSPy provides the prompt.
    - We request JSON output via a system-level instruction (Anthropic has no native JSON mode).
    - Some Claude models do not allow temperature + top_p together; we only use temperature.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        max_output_tokens: int = 768,
        temperature: float = 0.0,
        top_p: float = 1.0,
        thinking: Optional[Dict[str, Any]] = None,
        json_object: bool = True,
    ):
        super().__init__(model=model)
        self.client = client
        self.model = model

        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.thinking = thinking or {"type": "disabled"}

        self.json_object = json_object

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

        system_text = ""
        if self.json_object:
            system_text = (
                "You are an LLM-as-Judge. Follow the provided rubric exactly.\n\n"
                "Return ONLY a single JSON object with exactly these keys:\n"
                '  - "reasoning": string\n'
                '  - "final_score": integer\n'
                "Output ONLY the JSON object, no additional text."
            )

        req: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "thinking": self.thinking,
        }
        if system_text:
            req["system"] = system_text

        try:
            resp = self.client.messages.create(**req)
            if not resp.content:
                raw = json.dumps({"reasoning": f"Empty content from API, stop_reason={resp.stop_reason}", "final_score": -1}, ensure_ascii=False)
            else:
                raw = resp.content[0].text or ""
        except anthropic.APIError as e:
            status = getattr(e, "status_code", None)
            if status == 403:
                raw = json.dumps({"reasoning": "API error 403: content filter triggered", "final_score": -1}, ensure_ascii=False)
            else:
                raw = json.dumps({"reasoning": f"API error {status}: {str(e)}", "final_score": -1}, ensure_ascii=False)
        except anthropic.APIConnectionError as e:
            raw = json.dumps({"reasoning": f"Connection error: {str(e)}", "final_score": -1}, ensure_ascii=False)
        except anthropic.APITimeoutError as e:
            raw = json.dumps({"reasoning": f"Timeout error: {str(e)}", "final_score": -1}, ensure_ascii=False)
        except Exception as e:
            error_str = str(e)
            if "403" in error_str or "forbidden" in error_str.lower():
                raw = json.dumps({"reasoning": "API error 403: content filter triggered", "final_score": -1}, ensure_ascii=False)
            else:
                raw = json.dumps({"reasoning": f"Unexpected error: {error_str}", "final_score": -1}, ensure_ascii=False)

        self.last_latency_s = time.time() - t0
        self.last_raw_text = raw

        return [raw]
