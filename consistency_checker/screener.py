"""Surface screener (architecture: NLI path, parallel to the symbolic spine).

v1 implementation is a deterministic lexical heuristic, NOT a real NLI model:
it flags statement pairs that share most content words but differ in polarity
(one contains a negator, or one word is the negative-prefix form of a word in
the other). It exists for two reasons:

1. Fast feedback on surface contradictions while the symbolic path runs.
2. It is the permanently embedded baseline: anything only the solver finds
   (multi-hop chains) is, by construction, beyond this screener, which is
   exactly the competitive-advantage claim of the project.

Swap-in point for a real NLI cross-encoder: reimplement `screen` with the
same signature and return shape.
"""
from __future__ import annotations

import re
from itertools import combinations

from .vocabulary import NEG_PREFIXES

_NEGATORS = {"not", "no", "never", "cannot", "nothing", "none"}
_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "of", "who", "that",
    "their", "own", "with", "some", "every", "all", "each", "it", "its", "to",
    "and", "or", "in", "on", "for", "this", "these", "those",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def _content(tokens: set[str]) -> set[str]:
    return {t for t in tokens if t not in _STOP and t not in _NEGATORS}


def _prefix_antonyms(a: set[str], b: set[str]) -> list[str]:
    pairs = []
    for wa in a:
        for wb in b:
            for p in NEG_PREFIXES:
                if len(wa) - len(p) >= 3 and wa == p + wb:
                    pairs.append(f"{wa}/{wb}")
                elif len(wb) - len(p) >= 3 and wb == p + wa:
                    pairs.append(f"{wb}/{wa}")
    return pairs


def screen(items: list[tuple[str, str]]) -> list[dict]:
    """items: (statement_id, decontextualized_text). Returns flagged pairs."""
    toks = {sid: _tokens(text) for sid, text in items}
    flags: list[dict] = []
    for (ida, _), (idb, _) in combinations(items, 2):
        ca, cb = _content(toks[ida]), _content(toks[idb])
        if not ca or not cb:
            continue
        jaccard = len(ca & cb) / len(ca | cb)
        neg_a = bool(toks[ida] & _NEGATORS)
        neg_b = bool(toks[idb] & _NEGATORS)
        antonyms = _prefix_antonyms(ca, cb)
        if neg_a != neg_b and jaccard >= 0.5:
            flags.append({
                "a": ida, "b": idb, "jaccard": round(jaccard, 2),
                "signal": "shared wording with opposite polarity",
            })
        elif antonyms and jaccard >= 0.3:
            flags.append({
                "a": ida, "b": idb, "jaccard": round(jaccard, 2),
                "signal": f"prefix antonym pair: {', '.join(sorted(set(antonyms)))}",
            })
    return flags
