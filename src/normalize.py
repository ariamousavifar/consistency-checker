"""Extraction normalization (architecture stage 3 post-processing).

Deterministic safeguards driven by real live-run failures:

1. parse_statements: tolerate malformed LLM output. Models occasionally return
   a statement as a bare string, wrap the list in a dict, or include null/empty
   items. The old code did `ExtractedStatement(**item)` directly and crashed the
   whole example with "argument after ** must be a mapping, not str". This
   coerces what it can and skips what it cannot, never raising.

2. retype_bare_instances: a ground instance assertion ("NAME is a CLASS") that
   carries no derivation marker should be an AXIOM, not a derived_claim. Live
   runs typed "The blue is a whale" / "This document is a contract" as
   derived_claims, which dropped them out of the axiom base and collapsed whole
   entailment chains to not_entailed. This retype is conservative: it ONLY fires
   when there is no therefore/thus/so/hence/follows marker, so genuine
   conclusions ("therefore Socrates is mortal") keep their derived_claim type.

3. strip_dangling_guards: guarded-irreflexivity normalization (FOL-level, runs
   after vocabulary unification). See its docstring.
"""
from __future__ import annotations

import re

from .fol_parser import KEYWORDS, tokenize
from .schema import ExtractedStatement, GateOutcome, StatementType

# Markers that signal a statement is presented as a CONCLUSION, not a premise.
# If any appears, we never retype the statement to axiom.
_DERIVATION_MARKERS = re.compile(
    r"\b(therefore|thus|hence|so|consequently|it follows|follows that|"
    r"as a result|accordingly|then)\b",
    re.IGNORECASE,
)

# A bare ground instance: "<Name> is/was/are a/an <Class>", optionally negated.
# Subject is a proper name or simple determiner phrase; class may be multi-word
# ("a river city", "a domestic cat"); no logical connectives.
_INSTANCE = re.compile(
    r"^\s*(the\s+|a\s+|an\s+|this\s+|that\s+)?[A-Z0-9][\w'\-]*"
    r"(\s+[\w'\-]+){0,3}\s+(is|was|are|were)\s+(not\s+)?(a|an)\s+"
    r"[\w'\-]+(\s+[\w'\-]+){0,2}\.?\s*$",
    re.IGNORECASE,
)


def _coerce_item(item) -> dict | None:
    """Best-effort conversion of one raw extraction item into a kwargs dict."""
    if isinstance(item, dict):
        return item
    # Some models emit a plain string per statement; treat it as decontextualized
    # text and let the rest default. Without an id it cannot be referenced, so we
    # signal the caller to assign one.
    if isinstance(item, str) and item.strip():
        return {"_bare_text": item.strip()}
    return None


def parse_statements(data) -> list[ExtractedStatement]:
    """Turn whatever the extractor returned into a clean statement list.

    Accepts: a list of dicts (normal), a dict wrapping the list under a common
    key, a list containing stray strings/nulls, or junk. Never raises on shape.
    """
    # Unwrap a dict that contains the real list under a likely key.
    if isinstance(data, dict):
        for key in ("statements", "items", "result", "data", "extractions"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            # a single statement object
            data = [data]
    if not isinstance(data, list):
        return []

    out: list[ExtractedStatement] = []
    auto_id = 0
    for raw in data:
        item = _coerce_item(raw)
        if item is None:
            continue
        auto_id += 1
        if "_bare_text" in item:
            item = {
                "id": f"s{auto_id}",
                "type": "axiom",
                "original_text": item["_bare_text"],
                "decontextualized": item["_bare_text"],
            }
        # fill required fields defensively
        item.setdefault("id", f"s{auto_id}")
        item.setdefault("original_text", item.get("decontextualized", ""))
        item.setdefault("decontextualized", item.get("original_text", ""))
        item.setdefault("type", "axiom")
        if not item["decontextualized"] and not item["original_text"]:
            continue
        # tolerate an unknown type string
        try:
            StatementType(item["type"])
        except (ValueError, KeyError):
            item["type"] = "axiom"
        try:
            out.append(ExtractedStatement(**{
                k: v for k, v in item.items()
                if k in {"id", "type", "original_text", "decontextualized", "speaker", "depends_on"}
            }))
        except Exception:
            continue
    return out


# Guarded irreflexivity: `forall v. (P(v) -> not R(v, v))` -- a universal
# prohibition on a REFLEXIVE relational atom, gated by a single unary guard over
# the same bound variable. Matched against the whole normalized FOL string (the
# stable format vocabulary._detokenize emits), so nothing nested or partial can
# match by accident.
_GUARDED_IRREFLEXIVITY = re.compile(
    r"^forall\s+(\w+)\s*\.\s*\(\s*"          # forall v. (
    r"(\w+)\s*\(\s*\1\s*\)\s*->\s*"          #   P(v) ->
    r"(not\s+\w+\s*\(\s*\1\s*,\s*\1\s*\))"   #   not R(v, v)
    r"\s*\)$"                                 # )
)


def _predicate_occurrences(fol: str) -> list[str]:
    """Every predicate-position identifier in a FOL string (with repeats)."""
    try:
        toks = tokenize(fol)
    except Exception:
        return []
    return [t for i, t in enumerate(toks)
            if re.match(r"[A-Za-z_]\w*$", t) and t not in KEYWORDS
            and i + 1 < len(toks) and toks[i + 1] == "("]


def strip_dangling_guards(propositions) -> list[dict]:
    """Guarded-irreflexivity normalization (deterministic, document-scoped).

    The translator sometimes over-specifies a structural axiom with a type
    guard the document never instantiates: 'No person is an ancestor of
    themselves' emitted as `forall x. (Person(x) -> not Ancestor(x, x))` in a
    theory with ZERO ground `Person(...)` facts. The guard is then vacuous --
    `not Ancestor(x, x)` is never derivable -- and a real ancestry cycle goes
    silently unrefuted (the rel_genealogy_broken false negative).

    This pass strips the guard, yielding the truly universal axiom, ONLY when
    ALL of the following hold (deliberately razor-targeted -- widening the
    pattern risks manufacturing contradictions, see the unicorn test):
      1. the whole formula is exactly `forall v. (P(v) -> not R(v, v))`
         (single unary guard, consequent a NEGATED REFLEXIVE relational atom);
      2. the guard predicate P is a dangling type: across every accepted or
         bridge FOL in the document, P occurs ONLY in this guard position --
         never as a ground fact, never in any conclusion, never elsewhere.
    A populated type ('Person' with Person(adam) asserted) never fires; a
    positive-consequent rule ('all unicorns are immortal') never fires.

    Rewrites prop.fol in place with provenance in gate_reason; returns the
    provenance list for the run report.
    """
    active = [p for p in propositions
              if p.status == GateOutcome.ACCEPTED and p.fol]
    # Total predicate-position occurrences across the document.
    total: dict[str, int] = {}
    for p in active:
        for name in _predicate_occurrences(p.fol):
            total[name] = total.get(name, 0) + 1
    # Occurrences attributable to the guard position of the exact pattern.
    guarded: dict[str, int] = {}
    matches: list[tuple] = []   # (prop, match)
    for p in active:
        m = _GUARDED_IRREFLEXIVITY.match(p.fol.strip())
        if m:
            guard = m.group(2)
            guarded[guard] = guarded.get(guard, 0) + 1
            matches.append((p, m))

    provenance: list[dict] = []
    for p, m in matches:
        var, guard, consequent = m.group(1), m.group(2), m.group(3)
        if total.get(guard, 0) != guarded.get(guard, 0):
            continue    # the type is instantiated/used elsewhere: a real guard
        old = p.fol
        p.fol = f"forall {var}. ({consequent})"
        p.gate_reason = (p.gate_reason + " | " if p.gate_reason else "") + (
            f"dangling type-guard '{guard}' stripped (uninstantiated everywhere; "
            "guarded irreflexivity would be vacuously true and mask real cycles)"
        )
        provenance.append({
            "id": p.id, "guard": guard, "from": old, "to": p.fol,
            "reason": "guarded irreflexivity with an uninstantiated type guard",
        })
    return provenance


def retype_bare_instances(statements: list[ExtractedStatement]) -> list[ExtractedStatement]:
    """Retype ground instance assertions with no derivation marker to axiom.

    Conservative: only a derived_claim that (a) matches the bare-instance shape
    and (b) has no derivation marker in its original text is promoted. Genuine
    conclusions keep their type."""
    for st in statements:
        if st.type != StatementType.DERIVED_CLAIM:
            continue
        text = st.decontextualized.strip()
        original = st.original_text or ""
        if _DERIVATION_MARKERS.search(original) or _DERIVATION_MARKERS.search(text):
            continue
        if _INSTANCE.match(text):
            st.type = StatementType.AXIOM
    return statements
