"""Lemmatization for predicate canonicalization (architecture stage 4).

The live runs exposed the core recall bug: "taxation" (Taxation) and "taxes"
(Tax) are the same concept but plural-stripping alone keeps them distinct, so
no contradiction can form. This module collapses common morphological variants
to a shared root WITHOUT a heavy NLP dependency.

Strategy: an irregular table, then deterministic suffix rules tried
longest-first and applied to a fixed point. Nominalizing suffixes strip to the
bare stem (taxation->tax, happiness->happy via a 'i'->'y' repair). Rules only
fire above a minimum word length so short words are never mangled.

This is a precision-first placeholder for a real lemmatizer (spaCy) behind the
same `lemma` interface; swap by reimplementing `lemma`.
"""
from __future__ import annotations

_IRREGULAR = {
    "people": "person", "children": "child", "men": "man", "women": "woman",
    "feet": "foot", "teeth": "tooth", "mice": "mouse", "geese": "goose",
}

# (suffix, replacement, min_word_len). Tried longest-suffix-first.
_RULES = [
    ("ization", "", 8),
    ("isation", "", 8),
    ("ational", "", 9),
    ("ation", "", 6),
    ("ition", "ite", 7),
    (" ", "", 0),  # placeholder, skipped (len 0 guard below handles it)
    ("ness", "", 6),
    ("ments", "ment", 7),
    ("ment", "", 6),
    ("ities", "ity", 7),
    ("ity", "", 6),
    ("ence", "ent", 6),
    ("ance", "ant", 6),
    ("encies", "ent", 8),
    ("ancies", "ant", 8),
    ("ism", "", 5),
    ("ist", "", 5),
    ("ors", "", 5),
    ("ers", "", 5),
    ("or", "", 5),
    ("er", "", 5),
    ("ions", "", 6),
    ("ion", "", 6),
    ("ally", "al", 6),
    ("ical", "ic", 6),
    ("ies", "y", 5),
    ("ses", "s", 5),
    ("es", "", 5),
    ("s", "", 4),
]


def _repair(stem: str) -> str:
    # happi -> happy, citi -> city (nominalization left a trailing i)
    if len(stem) >= 4 and stem.endswith("i"):
        return stem[:-1] + "y"
    return stem


def _strip_once(word: str) -> str:
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    for suffix, repl, min_len in _RULES:
        if not suffix.strip():
            continue
        if len(word) >= min_len and word.endswith(suffix):
            stem = word[: len(word) - len(suffix)] + repl
            if len(stem) >= 3:
                return _repair(stem)
    return word


def lemma(word: str) -> str:
    """Reduce a single lowercase word toward a canonical root, to a fixed point
    (capped), e.g. nationalization -> national -> nation."""
    w = word.lower()
    for _ in range(3):
        nxt = _strip_once(w)
        if nxt == w:
            break
        w = nxt
    return w
