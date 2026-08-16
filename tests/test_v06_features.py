"""v0.6 tests: extraction robustness (no more hard crashes), instance retyping
(fixes entailment-chain collapse), adaptive fidelity threshold, and provider/
model resolution."""
from __future__ import annotations

import os

from consistency_checker.fidelity import fidelity_check
from consistency_checker.normalize import parse_statements, retype_bare_instances
from consistency_checker.schema import ExtractedStatement, StatementType


# ---------- Item 1: extraction robustness (no hard crashes) ----------

def test_parse_statements_handles_bare_strings():
    # the exact crash class: list contains strings, not dicts
    out = parse_statements(["Neptune is a planet", "It orbits the Sun"])
    assert len(out) == 2
    assert all(isinstance(s, ExtractedStatement) for s in out)


def test_parse_statements_unwraps_dict():
    data = {"statements": [
        {"id": "s1", "type": "axiom", "original_text": "a", "decontextualized": "a"}
    ]}
    out = parse_statements(data)
    assert len(out) == 1 and out[0].id == "s1"


def test_parse_statements_skips_junk_without_raising():
    out = parse_statements([
        {"id": "s1", "type": "axiom", "original_text": "a", "decontextualized": "a"},
        "stray string",
        None,
        42,
        {"type": "axiom"},  # missing text -> skipped
    ])
    # the valid dict plus the coercible string survive; null/int/empty are dropped
    assert len(out) == 2


def test_parse_statements_total_garbage_returns_empty():
    assert parse_statements("not a list at all") == []
    assert parse_statements(None) == []


def test_parse_statements_tolerates_unknown_type():
    out = parse_statements([
        {"id": "s1", "type": "nonsense", "original_text": "a", "decontextualized": "a"}
    ])
    assert out[0].type == StatementType.AXIOM


# ---------- Item 2: instance retyping (fixes chain collapse) ----------

def _mk(t, decon, orig=None):
    return ExtractedStatement(id="x", type=t, original_text=orig or decon, decontextualized=decon)


def test_bare_instance_retyped_to_axiom():
    cases = [
        _mk(StatementType.DERIVED_CLAIM, "The blue is a whale."),
        _mk(StatementType.DERIVED_CLAIM, "This document is a contract."),
        _mk(StatementType.DERIVED_CLAIM, "Old Ferry is a river city."),
        _mk(StatementType.DERIVED_CLAIM, "The tabby is a domestic cat."),
    ]
    for st in retype_bare_instances(cases):
        assert st.type == StatementType.AXIOM


def test_conclusion_with_marker_keeps_derived_type():
    cases = [
        _mk(StatementType.DERIVED_CLAIM, "Socrates is mortal.", "Therefore Socrates is mortal."),
        _mk(StatementType.DERIVED_CLAIM, "The tabby is a carnivore.", "So the tabby is a carnivore."),
        _mk(StatementType.DERIVED_CLAIM, "The tile is a quadrilateral.", "Thus the tile is a quadrilateral."),
    ]
    for st in retype_bare_instances(cases):
        assert st.type == StatementType.DERIVED_CLAIM


def test_non_instance_claims_unchanged():
    cases = [
        _mk(StatementType.DERIVED_CLAIM, "Some taxes are morally justified."),
        _mk(StatementType.DERIVED_CLAIM, "Every human is mortal."),
    ]
    for st in retype_bare_instances(cases):
        assert st.type == StatementType.DERIVED_CLAIM


def test_existing_axioms_untouched():
    a = _mk(StatementType.AXIOM, "The blue is a whale.")
    retype_bare_instances([a])
    assert a.type == StatementType.AXIOM


# ---------- Item 3: adaptive fidelity threshold ----------

def test_single_predicate_negated_instance_passes():
    # "Old Ferry has no population" -> not HasPopulation(old_ferry); previously
    # quarantined at coverage 0.5
    assert fidelity_check("not HasPopulation(old_ferry)", "Old Ferry has no population.").passed


def test_adaptive_threshold_still_rejects_wrong_single_predicate():
    assert not fidelity_check("Purple(elephant)", "The cat is small.").passed


# ---------- Items 4-5: provider/model resolution ----------

def test_provider_resolution_with_flags(monkeypatch):
    monkeypatch.setenv("NIM_API_KEY", "test_key_123")
    from consistency_checker.providers import resolve_model_config
    # The behaviour under test is suffix expansion -- a bare model name is
    # resolved to the fully-qualified id. The model itself is incidental, so use
    # one currently served by the endpoint; the previous choice was retired
    # upstream and made this test fail for a reason unrelated to what it checks.
    cfg = resolve_model_config("nim", "gemma-4-31b-it")
    assert cfg is not None
    assert cfg["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert cfg["model"] == "google/gemma-4-31b-it"  # suffix expanded to full id
    assert cfg["api_key"] == "test_key_123"
    assert cfg["thinking"] is True  # gemma is configured as a thinking model


def test_provider_resolution_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from consistency_checker.providers import resolve_model_config
    cfg = resolve_model_config("groq", "llama-3.3-70b-versatile")
    assert cfg is None  # no key -> fall back to .env


def test_gpt_oss_not_flagged_as_thinking(monkeypatch):
    monkeypatch.setenv("NIM_API_KEY", "k")
    from consistency_checker.providers import resolve_model_config
    cfg = resolve_model_config("nim", "openai/gpt-oss-120b")
    assert cfg["thinking"] is False


# ---------- LLMConfig override plumbing ----------

def test_llmconfig_overrides_take_priority(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    from consistency_checker.llm_client import LLMConfig
    cfg = LLMConfig(overrides={"base_url": "https://nim/v1", "model": "nim-model",
                               "api_key": "k", "thinking": True})
    assert cfg.base_url == "https://nim/v1"
    assert cfg.model == "nim-model"
    assert cfg.thinking is True


def test_llmconfig_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_API_KEY", "envkey")
    from consistency_checker.llm_client import LLMConfig
    cfg = LLMConfig()
    assert cfg.base_url == "https://env.example/v1"
    assert cfg.model == "env-model"
    assert cfg.thinking is False
