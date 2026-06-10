"""Vocabulary alignment (v1).

Predicates only contradict inside the solver if they share symbols, so every
FOL string passes through this registry before entering the gate's equivalence
check or the store. v1 normalization is deterministic: split a predicate name
into words, strip naive plurals, and map the resulting key onto the first
canonical form seen. An LLM adjudication step for harder merges (synonyms,
paraphrases) is a planned upgrade, not implemented here.
"""
from __future__ import annotations

import re

from .fol_parser import KEYWORDS, tokenize

_IDENT = re.compile(r"[A-Za-z_]\w*$")
_STOPWORDS = {"a", "an", "the"}


def _plural_strip(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words_of(name: str) -> list[str]:
    """Split CamelCase / snake_case into lowercase words."""
    parts = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name)
    return [p.lower() for p in parts]


def pred_key(name: str) -> str:
    return "".join(_plural_strip(w) for w in words_of(name))


class Vocabulary:
    def __init__(self) -> None:
        self._pred_by_key: dict[str, str] = {}
        self._const_by_key: dict[str, str] = {}

    @property
    def predicates(self) -> list[str]:
        return sorted(self._pred_by_key.values())

    @property
    def constants(self) -> list[str]:
        return sorted(self._const_by_key.values())

    def canonical_pred(self, name: str) -> str:
        key = pred_key(name)
        if key not in self._pred_by_key:
            camel = "".join(w.capitalize() for w in words_of(name))
            self._pred_by_key[key] = camel
        return self._pred_by_key[key]

    def canonical_const(self, name: str) -> str:
        key = name.lower()
        if key not in self._const_by_key:
            self._const_by_key[key] = key
        return self._const_by_key[key]

    def pred_from_phrase(self, phrase: str) -> str:
        """'seekers of truth' -> SeekerOfTruth (registered canonically)."""
        words = [w for w in re.split(r"[\s\-]+", phrase.strip().lower()) if w and w not in _STOPWORDS]
        words = [_plural_strip(re.sub(r"[^a-z0-9]", "", w)) for w in words]
        words = [w for w in words if w]
        if not words:
            raise ValueError(f"cannot build predicate from phrase {phrase!r}")
        return self.canonical_pred("".join(w.capitalize() for w in words))

    def normalize_fol(self, fol: str) -> str:
        """Rewrite predicate and constant identifiers in a FOL string to canonical forms."""
        toks = tokenize(fol)
        out: list[str] = []
        bound: set[str] = set()
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in ("forall", "exists"):
                out.append(t)
                i += 1
                while i < len(toks) and toks[i] != ".":
                    if toks[i] != ",":
                        bound.add(toks[i])
                    out.append(toks[i])
                    i += 1
                continue
            if _IDENT.match(t) and t not in KEYWORDS:
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt == "(":
                    out.append(self.canonical_pred(t))
                elif t in bound:
                    out.append(t)
                else:
                    out.append(self.canonical_const(t))
                i += 1
                continue
            out.append(t)
            i += 1
        return _detokenize(out)


def _detokenize(tokens: list[str]) -> str:
    out: list[str] = []
    prev = ""
    for t in tokens:
        if t in (")", ",", "."):
            out.append(t)
        elif t == "(":
            # no space between a predicate name and its argument list
            if prev and _IDENT.match(prev) and prev not in KEYWORDS:
                out.append(t)
            else:
                out.append(" " + t if out else t)
        else:
            out.append((" " if out and prev not in ("(",) else "") + t)
        prev = t
    return "".join(out).strip()
