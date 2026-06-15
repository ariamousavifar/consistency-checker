"""Vocabulary alignment (architecture stage 4).

Predicates only contradict inside the solver if they share symbols, so every
FOL string passes through this registry before the gate's equivalence check
and before entering the store. v0.3 components:

- Canonicalizer: deterministic case/plural normalization onto first-seen
  canonical forms (handles Humans/Human, taxes/tax, cities/city).
- Negation map: if a new predicate is the negative-prefix form of a known one
  (Immortal vs Mortal, Unjust vs Just, NonHuman vs Human), it is rewritten as
  `not P(x)` instead of becoming a fresh opaque symbol. This fixes the live-run
  failure where `Immortal(socrates)` made a real contradiction invisible to Z3.
  Direction matters: the base form must be registered first (premises usually
  precede the claims that negate them); the reverse direction is documented as
  a limitation.
- Bridge axioms live in the pipeline (user-supplied, tagged), not here: the
  registry never silently imports semantics, per the loyalty-to-text decision.
"""
from __future__ import annotations

import re

from .fol_parser import KEYWORDS, tokenize
from .lemmatizer import lemma

_IDENT = re.compile(r"[A-Za-z_]\w*$")
_STOPWORDS = {"a", "an", "the"}
NEG_PREFIXES = ("non", "un", "im", "ir", "dis", "in")


def _plural_strip(word: str) -> str:
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words_of(name: str) -> list[str]:
    """Split CamelCase / snake_case into lowercase words."""
    parts = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name)
    return [p.lower() for p in parts]


def pred_key(name: str) -> str:
    """Morphology-insensitive key: lemmatize each word so Tax/Taxation/taxes,
    Mortal/Mortality, etc. collapse to one predicate."""
    return "".join(lemma(w) for w in words_of(name))


class Vocabulary:
    def __init__(self) -> None:
        self._pred_by_key: dict[str, str] = {}
        self._neg_of: dict[str, str] = {}
        self._const_by_key: dict[str, str] = {}

    @property
    def predicates(self) -> list[str]:
        return sorted(self._pred_by_key.values())

    @property
    def constants(self) -> list[str]:
        return sorted(self._const_by_key.values())

    @property
    def negation_mappings(self) -> dict[str, str]:
        return dict(self._neg_of)

    def resolve_pred(self, name: str) -> tuple[str, bool]:
        """Return (canonical predicate, negated). Registers new predicates."""
        key = pred_key(name)
        if key in self._neg_of:
            return self._neg_of[key], True
        if key in self._pred_by_key:
            return self._pred_by_key[key], False
        ws = words_of(name)
        if len(ws) > 1 and ws[0] in ("not", "non"):
            base_key = "".join(lemma(w) for w in ws[1:])
            if base_key in self._pred_by_key:
                self._neg_of[key] = self._pred_by_key[base_key]
                return self._neg_of[key], True
        for p in NEG_PREFIXES:
            if key.startswith(p) and len(key) - len(p) >= 3 and key[len(p):] in self._pred_by_key:
                self._neg_of[key] = self._pred_by_key[key[len(p):]]
                return self._neg_of[key], True
        # Display name: readable (plural-stripped surface form), not the
        # aggressively-lemmatized key. Matching uses the key; humans see this.
        camel = "".join(_plural_strip(w).capitalize() for w in ws)
        self._pred_by_key[key] = camel
        return camel, False

    def canonical_pred(self, name: str) -> str:
        return self.resolve_pred(name)[0]

    def canonical_const(self, name: str) -> str:
        key = name.lower()
        if key not in self._const_by_key:
            self._const_by_key[key] = key
        return self._const_by_key[key]

    def resolve_phrase(self, phrase: str) -> tuple[str, bool]:
        """'immortal' -> (Mortal, True) once Mortal is known; 'seekers of truth' -> (SeekerOfTruth, False)."""
        words = [w for w in re.split(r"[\s\-]+", phrase.strip().lower()) if w and w not in _STOPWORDS]
        words = [re.sub(r"[^a-z0-9]", "", w) for w in words]
        words = [w for w in words if w]
        if not words:
            raise ValueError(f"cannot build predicate from phrase {phrase!r}")
        if words[0] in ("not", "non") and len(words) > 1:
            name, neg = self.resolve_pred("".join(w.capitalize() for w in words[1:]))
            return name, not neg
        return self.resolve_pred("".join(w.capitalize() for w in words))

    def pred_from_phrase(self, phrase: str) -> str:
        return self.resolve_phrase(phrase)[0]

    def normalize_fol(self, fol: str) -> str:
        """Rewrite predicates/constants to canonical forms; negation-mapped
        predicates are emitted as `not P`."""
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
                    canon, neg = self.resolve_pred(t)
                    if neg:
                        out.append("not")
                    out.append(canon)
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
            if prev and _IDENT.match(prev) and prev not in KEYWORDS:
                out.append(t)
            else:
                out.append(" " + t if out else t)
        else:
            out.append((" " if out and prev not in ("(",) else "") + t)
        prev = t
    return "".join(out).strip()
