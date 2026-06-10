"""Fidelity check (v1).

The target design is: deterministic verbalization + bidirectional NLI
entailment. v1 ships the deterministic half plus a lexical-coverage heuristic
instead of the NLI model, to keep the prototype dependency-light. The check
asks: do the content words of every predicate, and every constant, actually
appear (approximately) in the source proposition? It catches the most common
silent translation failure: the LLM inventing predicates or entities that the
sentence never mentioned.

Swap-in point for the NLI model: implement FidelityChecker.check with a
cross-encoder and keep the same return type.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .fol_parser import KEYWORDS, tokenize
from .vocabulary import words_of

_STOP = {"a", "an", "the", "of", "is", "are", "be", "to", "in"}


@dataclass
class FidelityResult:
    passed: bool
    coverage: float
    missing: list[str] = field(default_factory=list)
    verbalization: str = ""


def _symbols(fol: str) -> tuple[list[str], list[str]]:
    """Return (predicate content words, constants) from a FOL string."""
    toks = tokenize(fol)
    pred_words: list[str] = []
    consts: list[str] = []
    bound: set[str] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("forall", "exists"):
            i += 1
            while i < len(toks) and toks[i] != ".":
                if toks[i] != ",":
                    bound.add(toks[i])
                i += 1
            continue
        if re.match(r"[A-Za-z_]\w*$", t) and t not in KEYWORDS:
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt == "(":
                pred_words.extend(w for w in words_of(t) if w not in _STOP)
            elif t not in bound:
                consts.append(t.lower())
        i += 1
    return pred_words, consts


def _word_match(word: str, sentence_words: set[str]) -> bool:
    if word in sentence_words:
        return True
    if len(word) >= 4:
        stem = word[:4]
        return any(sw.startswith(stem) or word.startswith(sw[:4]) for sw in sentence_words if len(sw) >= 4)
    return False


def fidelity_check(fol: str, sentence: str, threshold: float = 0.6) -> FidelityResult:
    from .verbalizer import verbalize

    sentence_words = set(re.findall(r"[a-z0-9]+", sentence.lower()))
    pred_words, consts = _symbols(fol)
    missing: list[str] = []

    matched = 0
    for w in pred_words:
        if _word_match(w, sentence_words):
            matched += 1
        else:
            missing.append(w)
    coverage = matched / len(pred_words) if pred_words else 1.0

    consts_ok = True
    for c in consts:
        if not _word_match(c, sentence_words):
            consts_ok = False
            missing.append(c)

    try:
        verb = verbalize(fol)
    except Exception:
        verb = "(verbalization failed)"

    return FidelityResult(
        passed=coverage >= threshold and consts_ok,
        coverage=round(coverage, 3),
        missing=missing,
        verbalization=verb,
    )
