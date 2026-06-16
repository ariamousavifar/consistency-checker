"""Extraction normalization (architecture stage 3 post-processing).

Two deterministic safeguards applied to whatever the extraction judge returns,
both driven by real live-run failures:

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
"""
from __future__ import annotations

import re

from .schema import ExtractedStatement, StatementType

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
