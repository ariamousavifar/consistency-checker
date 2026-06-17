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
    def __init__(self, overrides: dict | None = None) -> None:
        overrides = overrides or {}
        self.base_url = overrides.get("base_url") or os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        self.api_key = overrides.get("api_key") or os.getenv("LLM_API_KEY", "")
        self.model = overrides.get("model") or os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "2048"))
        self.temperature = float(os.getenv("LLM_TEMPERATURE", "0"))
        # Some NIM reasoning models (DeepSeek V4, Qwen 3.5, Gemma 4) need an
        # explicit thinking toggle and may return content in reasoning_content.
        self.thinking = bool(overrides.get("thinking", False))
        # Gemini 3.x explicitly recommends NOT sending temperature/top_p/top_k;
        # the reasoning is tuned for defaults and the params can degrade output.
        self.omit_sampling = bool(overrides.get("omit_sampling", False))
        # Cerebras uses 'max_completion_tokens' instead of 'max_tokens'.
        self.max_tokens_param = overrides.get("max_tokens_param", "max_tokens")
        # Rate-limit pacing (free tiers are typically 30-40 requests/min). A
        # minimum interval between calls keeps a burst from tripping a 429; the
        # retry settings recover from one if it happens anyway.
        self.min_interval = float(os.getenv("LLM_MIN_INTERVAL", "1.5"))
        self.max_retries = int(os.getenv("LLM_MAX_RETRIES", "5"))

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


def _parse_retry_after(exc) -> float | None:
    """Pull a wait time (seconds) from a 429 error if the server provided one.

    Handles both numeric seconds ('2') and Groq-style durations ('2m59.56s').
    Looks in the retry-after header first, then the exception text.
    """
    # Try response headers via the OpenAI SDK exception shape.
    resp = getattr(exc, "response", None)
    header = None
    if resp is not None:
        try:
            header = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        except Exception:
            header = None
    text = header or str(exc)
    if not text:
        return None
    # numeric seconds
    m = re.search(r"retry[- ]after[\"']?\s*[:=]?\s*([0-9.]+)\s*s?\b", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # Groq-style "2m59.56s" or "7.66s"
    m = re.search(r"(?:(\d+)m)?([0-9.]+)s", text)
    if m and (m.group(1) or m.group(2)):
        mins = float(m.group(1) or 0)
        secs = float(m.group(2) or 0)
        return mins * 60 + secs
    if isinstance(header, str) and header.strip().replace(".", "", 1).isdigit():
        return float(header.strip())
    return None


def _is_rate_limit(exc) -> bool:
    if exc.__class__.__name__ == "RateLimitError":
        return True
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status == 429 or "429" in str(exc) or "too many requests" in str(exc).lower()


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        from openai import OpenAI

        self._client = OpenAI(base_url=self.config.base_url, api_key=self.config.api_key)
        self._last_call_ts = 0.0
        # Usage tracking (v0.7.5): accumulate across every API call so the
        # pipeline can report token cost and compare chunking vs single-pass.
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0

    def usage(self) -> dict:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def reset_usage(self) -> None:
        self.calls = self.prompt_tokens = self.completion_tokens = self.total_tokens = 0

    def _throttle(self) -> None:
        """Keep a minimum gap between calls so a burst stays under free-tier RPM."""
        gap = self.config.min_interval
        if gap <= 0:
            return
        elapsed = time.monotonic() - self._last_call_ts
        if elapsed < gap:
            time.sleep(gap - elapsed)

    def _create(self, **kwargs):
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception:
            # some endpoints reject extra_body (the thinking toggle); retry once without
            if "extra_body" in kwargs:
                kwargs.pop("extra_body", None)
                return self._client.chat.completions.create(**kwargs)
            raise

    def _raw(self, system: str, user: str) -> str:
        kwargs = {
            "model": self.config.model,
            self.config.max_tokens_param: self.config.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Gemini 3.x: omit temperature/top_p/top_k (recommended by Google; the
        # model is tuned for defaults). Every other provider gets temperature 0
        # for determinism.
        if not self.config.omit_sampling:
            kwargs["temperature"] = self.config.temperature
        # Reasoning models: disable thinking so we get a clean JSON answer rather
        # than a long chain-of-thought that buries (or replaces) the content.
        if self.config.thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": False}}

        # Rate-limit-aware retry loop with throttle + exponential backoff that
        # honors the server's retry-after when present.
        backoff = 5.0
        for attempt in range(self.config.max_retries + 1):
            self._throttle()
            try:
                resp = self._create(**kwargs)
                self._last_call_ts = time.monotonic()
                break
            except Exception as exc:
                self._last_call_ts = time.monotonic()
                if _is_rate_limit(exc) and attempt < self.config.max_retries:
                    wait = _parse_retry_after(exc)
                    if wait is None:
                        wait = backoff
                        backoff = min(backoff * 3, 60.0)
                    wait = min(max(wait, 1.0), 90.0)
                    print(f"  [rate limit] waiting {wait:.0f}s (attempt {attempt + 1}/{self.config.max_retries})...")
                    time.sleep(wait)
                    continue
                raise
        msg = resp.choices[0].message
        content = msg.content or ""
        if not content.strip():
            # reasoning models sometimes place the answer here
            content = getattr(msg, "reasoning_content", None) or ""
        # Accumulate usage (when the provider reports it).
        self.calls += 1
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        return content

    def complete_json(self, system: str, user: str, retries: int = 2):
        original_user = user
        current_user = user
        last_text = ""
        for attempt in range(retries + 1):
            # _raw now handles rate-limit retries internally; this loop is only
            # for JSON-shape self-correction.
            last_text = self._raw(system, current_user)
            for candidate in (_strip_fences(last_text), _balanced_extract(last_text) or ""):
                if not candidate:
                    continue
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            # Empty-response case (seen on Cerebras/long SEP text): the model
            # returned nothing at all. A "your output wasn't JSON" correction is
            # pointless - there's nothing to correct. Just retry the ORIGINAL task
            # cleanly; a blank reply is usually a transient truncation.
            if not last_text.strip():
                current_user = original_user
                continue
            # Non-empty but invalid JSON: self-correct while KEEPING the original
            # document in context (so the model doesn't lose the text and reply
            # "I don't have the content you're referring to").
            current_user = (
                f"{original_user}\n\n"
                "----\n"
                "IMPORTANT: your previous response to the task above was not valid JSON. "
                "Redo the SAME task on the SAME text above and return ONLY strictly valid "
                "JSON: no prose, no apologies, no markdown fences. If you cannot extract "
                "anything, return an empty JSON array []."
            )
        raise ValueError(f"could not obtain valid JSON from model; last output:\n{last_text[:500]}")
