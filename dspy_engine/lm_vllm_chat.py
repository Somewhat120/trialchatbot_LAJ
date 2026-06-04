# dspy_engine/lm_vllm_chat.py
# vLLM OpenAI-compatible /v1/chat/completions -> DSPy LM adapter (DSPy 3.x)

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import dspy
from openai import OpenAI


class VLLMChatLM(dspy.LM):
    """
    Minimal DSPy LM adapter for a vLLM server exposing OpenAI-compatible Chat Completions.

    We keep this intentionally simple and parity-aligned with your agent vLLM runner:
      - No JSON mode enforcement.
      - Returns raw text.
      - Supports both `prompt` and `messages` call styles from DSPy.

    We also support a model_profile-like message formatting:
      - message_style: "user_only" or "system_user"
      - content_style: "string" or "openai_text_list"
      - merge_system_into_user: bool (but you currently use system_role="" anyway)
    """

    def __init__(
        self,
        client: OpenAI,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        top_p: float = 1.0,
        *,
        message_style: str = "user_only",
        content_style: str = "openai_text_list",
        merge_system_into_user: bool = True,
        system_role: str = "",
        json_object: bool = False,
        disable_thinking: bool = False,
        sanitize_json_output: bool = False,
    ):
        super().__init__(model=model)
        self.client = client
        self.model = model

        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p

        self.message_style = message_style
        self.content_style = content_style
        self.merge_system_into_user = merge_system_into_user
        self.system_role = system_role or ""
        self.json_object = bool(json_object)
        self.disable_thinking = bool(disable_thinking)
        self.sanitize_json_output = bool(sanitize_json_output)

        self.last_latency_s: Optional[float] = None
        self.last_raw_text: str = ""

    @staticmethod
    def _strip_reasoning_wrappers(text: str) -> str:
        if not isinstance(text, str):
            return ""
        s = text
        s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL | re.IGNORECASE)
        s = re.sub(
            r"<(?:unused94|start_of_thought)>\s*(.*?)\s*<(?:unused95|end_of_thought)>",
            "",
            s,
            flags=re.DOTALL | re.IGNORECASE,
        )
        s = re.sub(
            r"<(?:unused94|start_of_thought)>\s*.*\Z",
            "",
            s,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return s.strip()

    @staticmethod
    def _extract_json_object(text: str) -> str:
        s = text.strip()
        l = s.find("{")
        r = s.rfind("}")
        if l != -1 and r != -1 and r > l:
            return s[l : r + 1].strip()
        return s

    def _wrap_content(self, text: str):
        if self.content_style == "openai_text_list":
            return [{"type": "text", "text": text}]
        return text

    def _build_messages(self, user_text: str) -> List[Dict[str, Any]]:
        sys_text = (self.system_role or "").strip()

        # merge_system_into_user only matters if sys_text exists
        if self.merge_system_into_user and sys_text:
            user_text = f"{sys_text}\n\n{user_text}".strip()
            sys_text = ""

        user_content = self._wrap_content(user_text)
        system_content = self._wrap_content(sys_text) if sys_text else None

        if self.message_style == "user_only":
            return [{"role": "user", "content": user_content}]
        if self.message_style == "system_user":
            msgs: List[Dict[str, Any]] = []
            if system_content:
                msgs.append({"role": "system", "content": system_content})
            msgs.append({"role": "user", "content": user_content})
            return msgs

        raise ValueError(f"Unknown message_style={self.message_style}")

    @staticmethod
    def _messages_to_text(messages: Any) -> str:
        # Best-effort flattening if DSPy passes messages instead of a prompt
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
                    content = m.get("content", "")
                    if isinstance(content, list):
                        txts = []
                        for blk in content:
                            if isinstance(blk, dict) and "text" in blk:
                                txts.append(str(blk["text"]))
                            else:
                                txts.append(str(blk))
                        parts.append("\n".join(txts))
                    else:
                        parts.append(str(content))
                else:
                    parts.append(str(m))
            return "\n".join(parts)
        return str(messages)

    def __call__(self, prompt: Optional[str] = None, **kwargs: Any) -> List[str]:
        t0 = time.time()

        if prompt is None:
            prompt = kwargs.get("prompt")
        if prompt is None:
            prompt = self._messages_to_text(kwargs.get("messages"))

        prompt = prompt or ""
        messages = self._build_messages(prompt)

        req: Dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=self.max_tokens,
        )
        if self.json_object:
            req["response_format"] = {"type": "json_object"}
        if self.disable_thinking:
            # Keep both keys for cross-template compatibility:
            # - some templates expect `enable_thinking`
            # - Baichuan-M2 template expects `thinking_mode`
            req["extra_body"] = {
                "chat_template_kwargs": {
                    "enable_thinking": False,
                    "thinking_mode": "off",
                }
            }

        resp = self.client.chat.completions.create(**req)

        raw = (resp.choices[0].message.content or "").strip()
        if self.sanitize_json_output:
            raw = self._extract_json_object(self._strip_reasoning_wrappers(raw))
        self.last_latency_s = time.time() - t0
        self.last_raw_text = raw
        return [raw]
