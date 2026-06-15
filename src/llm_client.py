"""Provider-agnostic LLM client (Groq, NVIDIA NIM, or any OpenAI-compatible endpoint).

Configured entirely from environment variables (see .env.example). Includes a
three-layer JSON robustness strategy: code-fence stripping, balanced-bracket
extraction, and one self-correction retry where the model is shown its own
broken output and asked to emit valid JSON only.
"""
from __future__ import annotations

import json
import os
import re
import time


class LLMConfig:
    def __init__(self) -> None:
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "2048"))
        self.temperature = float(os.getenv("LLM_TEMPERATURE", "0"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _balanced_extract(text: str) -> str | None:
    """Extract the first balanced JSON object or array from arbitrary text."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        from openai import OpenAI

        self._client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key)

    def _raw(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    def complete_json(self, system: str, user: str, retries: int = 2):
        last_text = ""
        for attempt in range(retries + 1):
            try:
                last_text = self._raw(system, user)
            except Exception:
                if attempt == retries:
                    raise
                time.sleep(2.0 * (attempt + 1))  # crude backoff for free-tier rate limits
                continue
            for candidate in (_strip_fences(last_text), _balanced_extract(last_text) or ""):
                if not candidate:
                    continue
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            # self-correction pass
            user = (
                "Your previous output was not valid JSON. Here it is:\n"
                f"{last_text}\n\nRe-emit the SAME content as strictly valid JSON. "
                "No prose, no markdown fences, JSON only."
            )
        raise ValueError(f"could not obtain valid JSON from model; last output:\n{last_text[:500]}")
