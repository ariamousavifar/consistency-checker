"""v0.6.1 tests: rate-limit parsing/detection, throttle config, new providers."""
from __future__ import annotations

from src.llm_client import LLMConfig, _is_rate_limit, _parse_retry_after


def test_parse_retry_after_numeric():
    assert _parse_retry_after(Exception("retry-after: 2")) == 2.0


def test_parse_retry_after_groq_duration():
    # "try again in 1m11.712s" -> 71.712
    val = _parse_retry_after(Exception("Please try again in 1m11.712s."))
    assert 71.0 < val < 72.0


def test_parse_retry_after_plain_seconds():
    assert abs(_parse_retry_after(Exception("wait 7.66s")) - 7.66) < 0.01


def test_parse_retry_after_none_when_absent():
    assert _parse_retry_after(Exception("some unrelated error")) is None


def test_is_rate_limit_detects_429():
    assert _is_rate_limit(Exception("Error code: 429 - Too Many Requests"))
    assert _is_rate_limit(Exception("too many requests"))


def test_is_rate_limit_ignores_other_errors():
    assert not _is_rate_limit(Exception("401 invalid key"))
    assert not _is_rate_limit(Exception("500 server error"))


def test_config_has_throttle_defaults(monkeypatch):
    monkeypatch.delenv("LLM_MIN_INTERVAL", raising=False)
    monkeypatch.delenv("LLM_MAX_RETRIES", raising=False)
    cfg = LLMConfig()
    assert cfg.min_interval == 1.5
    assert cfg.max_retries == 5


def test_config_throttle_from_env(monkeypatch):
    monkeypatch.setenv("LLM_MIN_INTERVAL", "3.0")
    monkeypatch.setenv("LLM_MAX_RETRIES", "8")
    cfg = LLMConfig()
    assert cfg.min_interval == 3.0
    assert cfg.max_retries == 8


def test_new_providers_registered(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from src.providers import load_registry, resolve_model_config
    reg = load_registry()
    assert {"nim", "groq", "cerebras", "google"} <= set(reg.keys())
    c = resolve_model_config("cerebras", "llama-3.3-70b")
    assert c["base_url"] == "https://api.cerebras.ai/v1"
    g = resolve_model_config("google", "gemini-2.0-flash")
    assert "generativelanguage.googleapis.com" in g["base_url"]
