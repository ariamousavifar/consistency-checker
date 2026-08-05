"""Pluggable semantic judge: natural-language entailment / equivalence.

Several stages need a semantic question answered that lexical heuristics only
approximate: the fidelity check ("does this FOL mean what the sentence says?")
and vocabulary alignment ("do the predicates `Fellow` and `FellowOfAcademy`
denote the same set here?"). This module defines one small interface so those
stages can stay backend-agnostic.

Design constraints (see fidelity.py / vocabulary.py):
- It must be OPTIONAL. When no judge is supplied the callers keep their existing
  deterministic lexical behaviour, so offline runs and the test suite stay
  dependency-free and reproducible.
- `LLMJudge` reuses the project's existing LLMClient -- no new dependency (no
  torch/transformers). A local DeBERTa-MNLI cross-encoder can later implement the
  same `SemanticJudge` interface as a drop-in replacement.
- Calls are CACHED and symmetric for equivalence, so a document with repeated
  predicates does not re-ask the model the same question.

`entails`/`equivalent` return Optional[bool]: True/False is a confident answer,
None means "could not decide" (e.g. malformed model reply) so callers can fall
back rather than treat uncertainty as a hard no.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SemanticJudge(Protocol):
    def entails(self, premise: str, hypothesis: str) -> bool | None:
        """True if PREMISE makes HYPOTHESIS necessarily true; None if undecided."""
        ...

    def equivalent(self, a: str, b: str) -> bool | None:
        """True if a and b mutually entail (same truth conditions); None if undecided."""
        ...


_ENTAIL_SYSTEM = (
    "You are a strict natural-language inference judge for a logic pipeline. "
    "Given PREMISE and HYPOTHESIS, decide whether the premise LOGICALLY ENTAILS "
    "the hypothesis: if the premise is true, must the hypothesis also be true? "
    "Judge only the literal logical content -- do not add world knowledge, do "
    "not treat mere topical relatedness as entailment. "
    'Output ONLY JSON: {"entails": true} or {"entails": false}. '
    "No prose, no markdown."
)

_EQUIV_SYSTEM = (
    "You are a strict natural-language equivalence judge for a logic pipeline. "
    "Given statement A and statement B, decide whether they have the SAME truth "
    "conditions in context -- each one entails the other (mutual entailment). "
    "Two class terms that refer to the same group here (e.g. 'a fellow' and 'a "
    "fellow of the Academy' when the Academy is the only context) count as "
    "equivalent; terms that pick out DIFFERENT groups (e.g. 'a resident of "
    "France' vs 'a resident of Germany') do NOT. Judge literal content only. "
    'Output ONLY JSON: {"equivalent": true} or {"equivalent": false}. '
    "No prose, no markdown."
)


class LLMJudge:
    """SemanticJudge backed by the existing LLMClient (temperature 0 + cache)."""

    def __init__(self, client) -> None:
        self._client = client
        self._entail_cache: dict[tuple[str, str], bool | None] = {}
        self._equiv_cache: dict[tuple[str, str], bool | None] = {}

    @staticmethod
    def _norm(s: str) -> str:
        return " ".join(s.strip().split())

    def _ask(self, system: str, key_field: str, a_label: str, a: str, b_label: str, b: str) -> bool | None:
        payload = f"{a_label}: {a}\n{b_label}: {b}"
        try:
            data = self._client.complete_json(system, payload)
        except Exception:
            return None
        if isinstance(data, dict) and key_field in data:
            val = data[key_field]
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ("true", "yes", "1")
        return None

    def entails(self, premise: str, hypothesis: str) -> bool | None:
        p, h = self._norm(premise), self._norm(hypothesis)
        if not p or not h:
            return None
        key = (p, h)
        if key not in self._entail_cache:
            self._entail_cache[key] = self._ask(
                _ENTAIL_SYSTEM, "entails", "PREMISE", p, "HYPOTHESIS", h
            )
        return self._entail_cache[key]

    def equivalent(self, a: str, b: str) -> bool | None:
        x, y = self._norm(a), self._norm(b)
        if not x or not y:
            return None
        if x == y:
            return True
        key = (x, y) if x <= y else (y, x)  # symmetric cache
        if key not in self._equiv_cache:
            self._equiv_cache[key] = self._ask(
                _EQUIV_SYSTEM, "equivalent", "A", x, "B", y
            )
        return self._equiv_cache[key]
