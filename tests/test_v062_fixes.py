"""v0.6.2 tests: self-correction must preserve the original document (the bug
that crashed long real-world texts), and Gemini config (correct IDs + sampling
omission)."""
from __future__ import annotations

import os

from src.llm_client import LLMClient, LLMConfig


def _client_with_fake_raw(responses):
    """Build an LLMClient whose _raw returns queued responses, recording inputs."""
    cfg = LLMConfig(overrides={"base_url": "x", "api_key": "k", "model": "m"})
    client = LLMClient.__new__(LLMClient)
    client.config = cfg
    client._last_call_ts = 0.0
    seen = []
    seq = iter(responses)

    def fake_raw(system, user, reasoning_effort=None):
        seen.append(user)
        return next(seq)

    client._raw = fake_raw
    return client, seen


def test_self_correction_preserves_original_document():
    # first reply is prose (not JSON); retry must still contain the document
    client, seen = _client_with_fake_raw([
        "Sorry, here's some prose instead of JSON.",
        '[{"id":"s1","type":"axiom","original_text":"a","decontextualized":"a"}]',
    ])
    result = client.complete_json("SYS", "DOCUMENT ABOUT NEPTUNE", retries=2)
    assert result[0]["id"] == "s1"
    # the bug: retry used to drop the document. Guard against regression.
    assert "DOCUMENT ABOUT NEPTUNE" in seen[1]
    assert "valid JSON" in seen[1]


def test_self_correction_recovers_within_retry_budget():
    client, seen = _client_with_fake_raw([
        "nope",
        "still nope",
        '{"ok": true}',
    ])
    result = client.complete_json("SYS", "DOC", retries=2)
    assert result == {"ok": True}
    assert len(seen) == 3
    # every retry kept the original doc
    assert all("DOC" in msg for msg in seen)


def test_gemini_config_omits_sampling(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from src.providers import resolve_model_config
    g = resolve_model_config("google", "gemini-3.5-flash")
    assert g["omit_sampling"] is True
    cfg = LLMConfig(overrides=g)
    assert cfg.omit_sampling is True


def test_non_gemini_keeps_sampling(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    from src.providers import resolve_model_config
    gr = resolve_model_config("groq", "llama-3.3-70b-versatile")
    assert gr.get("omit_sampling", False) is False


def test_gemini_model_ids_current(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    from src.providers import load_registry
    models = load_registry()["google"]["models"]
    # the GA model from the docs must be present; stale 2.0/1.5 ids gone
    assert "gemini-3.5-flash" in models
    assert "gemini-2.0-flash" not in models
