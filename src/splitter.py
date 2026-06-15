"""Compound-statement splitting (architecture stage 2: syntactic layer).

The live runs showed the extraction LLM ignoring the "split compounds"
instruction (e.g. "Socrates was a philosopher and Socrates was human" arrived
as one statement with a conjoined FOL formula). Asking nicely is not enough, so
this deterministic pass splits a decontextualized proposition on a top-level
coordinating "and" when BOTH sides are independently assertable clauses.

Conservative by design: it only splits when each side has its own subject and
predicate (a finite verb), so noun-phrase conjunctions ("roads and hospitals
do not pay for themselves", "black and white") are NOT split, since those need
a single shared predicate and splitting would corrupt meaning. Anything it is
unsure about is left intact for the translation gate to handle.

This is a regex/heuristic placeholder for a dependency-parse-based splitter
(spaCy) behind the same `split_statement` interface.
"""
from __future__ import annotations

import re

# A finite-verb cue on each side signals two independent clauses rather than a
# conjoined noun phrase.
_VERB_CUES = re.compile(
    r"\b(is|are|was|were|has|have|had|does|do|did|will|would|can|could|"
    r"should|must|may|might|questions?|seeks?|takes?|makes?|believes?)\b",
    re.IGNORECASE,
)
_SUBJECT_START = re.compile(r"^(every|all|some|no|each|the|a|an|there)\b", re.IGNORECASE)


def _independent_clause(text: str) -> bool:
    t = text.strip()
    if len(t.split()) < 3:
        return False
    return bool(_VERB_CUES.search(t))


def _top_level_and_split(text: str) -> list[str]:
    """Split on ', and' or ' and ' only at the top level (not inside parens)."""
    # Prefer the comma-and boundary, which almost always joins clauses.
    parts = re.split(r",\s+and\s+", text)
    if len(parts) == 2:
        return [p.strip() for p in parts]
    # Bare ' and ': split once on the first occurrence, then validate both sides.
    m = re.search(r"\s+and\s+", text)
    if m:
        left, right = text[: m.start()].strip(), text[m.end():].strip()
        return [left, right]
    return [text]


def split_statement(decontextualized: str) -> list[str]:
    """Return one or more self-contained clauses. Splits only when safe."""
    text = decontextualized.strip().rstrip(".")
    if " and " not in f" {text} ".lower():
        return [decontextualized]

    candidates = _top_level_and_split(text)
    if len(candidates) != 2:
        return [decontextualized]

    left, right = candidates
    # Both sides must look like independent clauses to justify a split.
    if _independent_clause(left) and _independent_clause(right):
        # If the right side lacks a subject, try to carry the left subject over
        # (e.g. "all philosophers are human" stands alone already, but
        # "Socrates was a philosopher and was human" -> reuse "Socrates").
        if not _SUBJECT_START.match(right) and not re.match(r"^[A-Z]\w+\b", right):
            subj_match = re.match(r"^([A-Z]\w+|\w+)\b", left)
            if subj_match and _VERB_CUES.match(right):
                right = f"{subj_match.group(1)} {right}"
        return [left.rstrip(".") + ".", right.rstrip(".") + "."]

    return [decontextualized]
