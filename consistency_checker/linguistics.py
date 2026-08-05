"""Deterministic lexical heuristics over statement text (no LLM, reproducible).

Two jobs, both feeding decisions the symbolic layer can't make on its own:

1. hedge_cue() -- detect GENERIC / defeasible generalizations ("birds typically
   fly", "ceteris paribus ...") so the gate can keep them out of the strict
   `forall` axiom set. A generic with exceptions is not a logical universal, and
   formalizing it as one manufactures false contradictions ("penguins are birds
   that don't fly"). This is the false-positive guard for real-world prose, which
   is saturated with hedged claims.

2. quarantine_shape() -- bucket a statement that fell OUTSIDE the unary FOL
   fragment by the kind of expressiveness it needs (relational, modal, numeric,
   ...). Aggregated over a document, the histogram tells us which logic to target
   next (EPR vs description logic vs modal), replacing guesswork with measurement.

Both are coarse and lexical: per statement they will misfire; in aggregate, and
for the conservative job of hedge-guarding, they are sound enough. Bare-plural
genericity ("Birds fly", no cue word) is a genuinely hard disambiguation we do
NOT attempt -- a documented limitation, not an oversight.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z]+")

# --- 1. hedge / defeasibility detection -------------------------------------

# Multi-word cues checked as substrings (order-independent).
_HEDGE_PHRASES = (
    "ceteris paribus",
    "other things equal",
    "other things being equal",
    "all else equal",
    "all else being equal",
    "as a rule",
    "as a general rule",
    "as a general matter",
    "in general",
    "for the most part",
    "by and large",
    "more often than not",
    "in most cases",
    "rule of thumb",
)

# Single-word frequency/genericity adverbs. Deliberately EXCLUDES counting words
# (most/many/few) -- those are precise quantifiers, not defeasibility markers, and
# are caught by the shape classifier as comparative-numeric instead. Also excludes
# precision hedges (roughly/approximately) which are about a number, not a rule.
_HEDGE_WORDS = {
    "typically", "usually", "generally", "normally", "ordinarily",
    "often", "frequently", "mostly", "commonly", "customarily",
    "tend", "tends", "tended", "tendency", "tendencies",
}


def hedge_cue(*texts: str) -> str | None:
    """Return the first explicit hedge/defeasibility cue found across the given
    texts (e.g. decontextualized + original), or None. Checking both catches the
    case where decontextualization silently strips a 'typically' and turns a
    generic into a strict universal -- the exact bug this guards against."""
    for text in texts:
        if not text:
            continue
        low = text.lower()
        for ph in _HEDGE_PHRASES:
            if ph in low:
                return ph
        hit = {w for w in _WORD_RE.findall(low)} & _HEDGE_WORDS
        if hit:
            return sorted(hit)[0]
    return None


# --- 1b. deontic / prescriptive detection -----------------------------------

# Normative ('ought') cues, as distinct from descriptive ('is') claims. Used by
# the OPT-IN deontic guard (paired with --allow-conditionals): once conditional
# and deontic structure is admitted, a prescriptive claim formalized like a fact
# can manufacture is/ought false contradictions ('citizens should obey the law'
# vs 'this citizen does not obey'). Quarantining keeps norms out of the
# descriptive axiom set. Multi-word cues first, then word-boundary singles.
# Bare 'may'/'right' are excluded -- too ambiguous (epistemic 'may', noun 'right').
_DEONTIC_PHRASES = (
    "have to", "has to", "had to", "ought to", "supposed to", "right to",
    "entitled to", "obligation to", "duty to", "may not", "must not",
    "permitted to", "required to", "allowed to", "free to",
)
_DEONTIC_WORDS = {
    "must", "should", "shall", "ought", "obligated", "obliged",
    "required", "permitted", "prohibited", "forbidden", "entitled",
    "impermissible", "obligatory", "mandatory", "permissible",
}


def deontic_cue(*texts: str) -> str | None:
    """Return the first deontic/prescriptive cue across the texts, or None.
    Mirrors hedge_cue: phrases as substrings, single words on a boundary."""
    for text in texts:
        if not text:
            continue
        low = text.lower()
        for ph in _DEONTIC_PHRASES:
            if ph in low:
                return ph
        hit = {w for w in _WORD_RE.findall(low)} & _DEONTIC_WORDS
        if hit:
            return sorted(hit)[0]
    return None


# --- 2. quarantine-shape classifier -----------------------------------------

# Each bucket is a set of cue tokens/substrings. Checked in the order below;
# earlier (more specific / higher-value) buckets win on overlap.
_MODAL_DEONTIC = (
    "must", "should", "ought", "shall", "may not", "obligated", "obliged",
    "required", "permitted", "prohibited", "forbidden", "allowed", "entitled",
    "necessarily", "possibly", "have to", "has to", "cannot", "duty", "right to",
)
_CAUSAL = (
    "because", "cause", "causes", "caused", "causing", "leads to", "lead to",
    "results in", "result in", "due to", "therefore", "hence", "thus",
    "consequently", "brings about", "gives rise", "so that", "owing to",
)
_NUMERIC_COMPARATIVE = (
    " than ", "more than", "less than", "fewer", "at least", "at most",
    "majority", "minority", "percent", "%", "greater", "smaller", "larger",
    "exceeds", "outnumber", "twice", "double", "half", "proportion",
)
_TRANSITIVE_ORDER = (
    "before", "after", "precedes", "preceded", "follows", "followed",
    "earlier than", "later than", "part of", "contains", "within", "inside",
    "above", "below", "ancestor", "descendant", "north of", "south of",
    "ranks above", "ranks below", "higher than", "lower than",
)
# Relational prepositions/verbs that link two entities. Padded with spaces so we
# match whole words; common-but-weak ('of'/'in') included since the residual
# relational bucket is meant to be permissive.
_RELATIONAL = (
    " in ", " of ", " on ", " at ", " owns ", " own ", " has ", " have ",
    " belongs ", " belong ", " member ", " located ", " located in ", " to ",
    " from ", " with ", " between ", " among ", " governs ", " elects ",
    " appoints ", " keeps ", " serves ", " stored ", " bound ",
)
_UNIVERSAL = ("every ", "all ", "each ", "any ", "no ")


def _has(low: str, cues) -> bool:
    return any(c in low for c in cues)


def quarantine_shape(text: str) -> str:
    """Coarse bucket for a statement outside the unary fragment. Heuristic: use
    aggregate proportions, not individual labels, to choose the next logic."""
    if not text:
        return "other"
    low = f" {text.lower()} "
    if _has(low, _MODAL_DEONTIC):
        return "modal-deontic"
    if _has(low, _NUMERIC_COMPARATIVE):
        return "comparative-numeric"
    if _has(low, _CAUSAL):
        return "causal"
    if _has(low, _TRANSITIVE_ORDER):
        return "transitive-ordering"
    if _has(low, _RELATIONAL):
        # A universal subject + a relation is the ∀∃ role-restriction shape that
        # EPR cannot express but description logic can -- the decisive bucket for
        # the EPR-vs-DL question, so it is separated out from ground relations.
        if _has(low, _UNIVERSAL):
            return "relational-role(∀∃)"
        return "relational-ground"
    return "other"
