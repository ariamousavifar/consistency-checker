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

# A conditional/disjunctive sentence is a SINGLE inference rule: tearing its
# antecedent and consequent apart (or splitting the 'and' that joins the two
# halves of an antecedent) destroys the logic. The 'then' clause also reads as a
# participial adjunct to _adjunct_split, so this must be checked first. Matches a
# leading or clause-introducing if/when/whenever/either.
_CONDITIONAL = re.compile(r"(^|[\s,;:])(if|when|whenever|either)\b", re.IGNORECASE)

# Appositive / participial / relative adjuncts hang an extra clause off a
# self-contained copular fact: "Aldous is a laureate, having been elevated ...".
# The extractor often keeps the whole thing as one statement, and the trailing
# adjunct (tense/participial/relative) pushes it outside the FOL fragment, so the
# atomic fact "Aldous is a laureate" is lost with it. We peel the fact off and
# let the adjunct become its own statement (which then quarantines harmlessly).
_ADJUNCT_CUE = re.compile(
    r"^(having|being|who|whom|whose|which|that|where|when|since|now|then)\b", re.IGNORECASE
)
_COPULAR = re.compile(r"\b(?:is|was)\s+(?:a|an)\s+\w", re.IGNORECASE)
# A subject-led copular instance followed by a bare relative clause, no comma:
# "Aldous is a laureate who was elevated ..." -> the relative needs the subject
# carried over. Restricted to a proper-name or pronoun subject to stay safe.
_REL_NO_COMMA = re.compile(
    r"^((?:[A-Z][\w']*|He|She|It|They)\s+(?:is|was)\s+(?:a|an)\s+[\w-]+(?:\s+[\w-]+){0,2}?)"
    r"\s+(?:who|which|that)\s+(.+)$"
)


def _adjunct_split(text: str) -> list[str] | None:
    # comma-introduced participial/relative adjunct
    if "," in text:
        left, _, right = text.partition(",")
        left, right = left.strip(), right.strip()
        if _ADJUNCT_CUE.match(right) and _COPULAR.search(left) and len(left.split()) >= 3:
            return [left.rstrip(".") + ".", right.rstrip(".") + "."]
    # bare relative clause off a named subject
    m = _REL_NO_COMMA.match(text)
    if m:
        head, rest = m.group(1).strip(), m.group(2).strip()
        subj = head.split()[0]
        return [head.rstrip(".") + ".", f"{subj} {rest}".rstrip(".") + "."]
    return None


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

    # Leave a conditional/disjunctive sentence whole -- it is one inference rule.
    if _CONDITIONAL.search(" " + text):
        return [decontextualized]

    # Peel a copular fact off a trailing participial/relative adjunct first, so
    # "X is a Y, having ..." / "X is a Y who ..." yields the atomic "X is a Y".
    adjunct = _adjunct_split(text)
    if adjunct:
        return adjunct

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
