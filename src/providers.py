"""Provider and model selection (CLI flags + interactive picker).

Resolution order for which endpoint/model to use:
1. --provider / --model CLI flags (highest priority)
2. interactive numbered menu, IF a terminal is attached and flags are missing
3. .env values (LLM_BASE_URL / LLM_MODEL) as the non-interactive fallback

The API key is never selected interactively; it is read from the environment
using the provider's api_key_env candidates (first one set wins), so secrets
stay in .env and out of providers.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REGISTRY_PATH = Path("providers.json")


def load_registry() -> dict:
    try:
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))["providers"]
    except Exception:
        return {}


def _resolve_api_key(provider_cfg: dict) -> str | None:
    for env_name in provider_cfg.get("api_key_env", []):
        val = os.environ.get(env_name)
        if val:
            return val
    return None


def _prompt_choice(title: str, options: list[str]) -> int | None:
    print(f"\n{title}")
    for i, opt in enumerate(options, start=1):
        print(f"  {i}) {opt}")
    while True:
        try:
            raw = input("> ").strip()
        except EOFError:
            return None
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print(f"  enter a number 1-{len(options)}")


def resolve_model_config(provider_flag: str | None, model_flag: str | None) -> dict | None:
    """Return a dict {base_url, model, api_key, thinking} or None to fall back
    to the .env-based LLMConfig.

    None is returned when no flags are given AND no terminal is attached, so
    headless/offline runs keep working off .env.
    """
    registry = load_registry()
    if not registry:
        return None

    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    # Provider
    provider_key = provider_flag
    if provider_key and provider_key not in registry:
        # tolerate label or case differences
        for k, v in registry.items():
            if provider_flag.lower() in (k.lower(), v.get("label", "").lower()):
                provider_key = k
                break
    if not provider_key:
        if not interactive:
            return None
        keys = list(registry.keys())
        labels = [f"{registry[k]['label']} ({k})" for k in keys]
        idx = _prompt_choice("Select provider:", labels)
        if idx is None:
            return None
        provider_key = keys[idx]

    if provider_key not in registry:
        print(f"Unknown provider '{provider_key}'; falling back to .env.")
        return None
    pcfg = registry[provider_key]

    # Model
    model = model_flag
    if model and model not in pcfg["models"]:
        # allow a short suffix match e.g. "qwen3.5-122b-a10b"
        for m in pcfg["models"]:
            if m.endswith(model) or m.split("/")[-1] == model:
                model = m
                break
    if not model:
        if not interactive:
            return None
        idx = _prompt_choice(f"Select model for {pcfg['label']}:", pcfg["models"])
        if idx is None:
            return None
        model = pcfg["models"][idx]

    api_key = _resolve_api_key(pcfg)
    if not api_key:
        envs = " or ".join(pcfg.get("api_key_env", []))
        print(f"No API key found for {pcfg['label']} (set {envs} in .env).")
        return None

    thinking = model in pcfg.get("thinking_models", [])
    omit_sampling = bool(pcfg.get("omit_sampling", False))
    max_tokens_param = pcfg.get("max_tokens_param", "max_tokens")
    # Per-model reasoning depth. gpt-oss exposes low/medium/high (no full off, so
    # "low" is the floor); GLM additionally accepts "none" to disable reasoning.
    # Capping it stops a reasoning model from spending its whole completion budget
    # on chain-of-thought and returning empty content -- the Cerebras empty-JSON
    # failures (finish_reason=length before any answer tokens are emitted).
    # Env override wins (LLMConfig applies it too); read it here so the header
    # reports the EFFECTIVE effort, not just the registry default.
    reasoning_effort = os.getenv("LLM_REASONING_EFFORT") or pcfg.get("reasoning_effort", {}).get(model)
    print(f"\nRunning with {pcfg['label']} / {model}"
          f"{' [thinking model]' if thinking else ''}"
          f"{f' [reasoning_effort={reasoning_effort}]' if reasoning_effort else ''}\n")
    return {
        "base_url": pcfg["base_url"],
        "model": model,
        "api_key": api_key,
        "thinking": thinking,
        "omit_sampling": omit_sampling,
        "max_tokens_param": max_tokens_param,
        "reasoning_effort": reasoning_effort,
    }
