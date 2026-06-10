"""Rule-based NL -> FOL translator (the deterministic half of the translation gate).

Precision-first: it handles a small controlled fragment of decontextualized
English and returns None for everything outside it. Refusal is intended; those
sentences fall through to the LLM translator. The fragment:

    1. "all/every/each X are/is Y"        -> forall x. (X(x) -> Y(x))
    2. "no X are/is Y"                    -> forall x. (X(x) -> not Y(x))
    3. "some X are Y"                     -> exists x. (X(x) and Y(x))
    4. "<name> is a/an X"                 -> X(name)
    5. "<name> is not a/an X"             -> not X(name)

Sentences containing relative clauses or connective markers (who, that, which,
if, then, because, unless, and, or) are refused: they need compositional
treatment the rules cannot guarantee.
"""
from __future__ import annotations

import re

from .vocabulary import Vocabulary

_REFUSE = re.compile(r"\b(who|whom|whose|that|which|if|then|because|unless|when|while|and|or|but)\b")
_QUANT_WORDS = {"all", "every", "each", "some", "no", "everyone", "someone", "nobody", "everything"}

_UNIVERSAL = re.compile(r"^(?:all|every|each)\s+(.+?)\s+(?:are|is)\s+(?:a\s+|an\s+)?(.+)$")
_UNIVERSAL_NEG = re.compile(r"^no\s+(.+?)\s+(?:are|is)\s+(?:a\s+|an\s+)?(.+)$")
_EXISTENTIAL = re.compile(r"^(?:some|there\s+are|there\s+is)\s+(.+?)\s+(?:are|is)\s+(?:a\s+|an\s+)?(.+)$")
_INSTANCE_NEG = re.compile(r"^([a-z][\w]*)\s+(?:is|was)\s+not\s+(?:a\s+|an\s+)?(.+)$")
_INSTANCE = re.compile(r"^([a-z][\w]*)\s+(?:is|was)\s+(?:a\s+|an\s+)?(.+)$")


def rule_translate(sentence: str, vocab: Vocabulary) -> str | None:
    s = sentence.strip()
    s = re.sub(r"[.!]+\s*$", "", s).strip().lower()
    if not s or _REFUSE.search(s):
        return None

    m = _UNIVERSAL_NEG.match(s)
    if m:
        p = vocab.pred_from_phrase(m.group(1))
        q = vocab.pred_from_phrase(m.group(2))
        return f"forall x. ({p}(x) -> not {q}(x))"

    m = _UNIVERSAL.match(s)
    if m:
        p = vocab.pred_from_phrase(m.group(1))
        q = vocab.pred_from_phrase(m.group(2))
        return f"forall x. ({p}(x) -> {q}(x))"

    m = _EXISTENTIAL.match(s)
    if m:
        p = vocab.pred_from_phrase(m.group(1))
        q = vocab.pred_from_phrase(m.group(2))
        return f"exists x. ({p}(x) and {q}(x))"

    m = _INSTANCE_NEG.match(s)
    if m and m.group(1) not in _QUANT_WORDS:
        c = vocab.canonical_const(m.group(1))
        p = vocab.pred_from_phrase(m.group(2))
        return f"not {p}({c})"

    m = _INSTANCE.match(s)
    if m and m.group(1) not in _QUANT_WORDS:
        c = vocab.canonical_const(m.group(1))
        p = vocab.pred_from_phrase(m.group(2))
        return f"{p}({c})"

    return None
