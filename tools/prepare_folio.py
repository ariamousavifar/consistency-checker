"""Adapt FOLIO into (a) a contradiction set and (b) a translation-accuracy set.

FOLIO pairs naturalistic English premises with first-order logic written by human
annotators, plus an entailment label. Two things follow from that, and this script
produces both.

(a) Contradiction detection. As with ProofWriter, entailment converts to
inconsistency: label True means the premises entail the conclusion, so premises
plus the negated conclusion is inconsistent. FOLIO conclusions are ordinary
English sentences rather than a fixed template, so negation is done by prefixing
"It is not the case that ...", which is faithful for any declarative sentence and
requires no judgement about its internal structure.

(b) Translation accuracy -- the more valuable output. FOLIO ships `premises-FOL`,
so our translator's formula for each premise can be compared against a human's.
This measures the stage we have diagnosed as the bottleneck, rather than
inferring its quality from end-to-end recall. Comparison is by Z3 equivalence
where both parse, not string equality, because `forall x. P(x)` and
`forall y. P(y)` are the same proposition and a string comparison would score
correct translations as wrong.

Note on scoring (a): FOLIO's `label` values are True / False / Uncertain. Only
True and Uncertain are used -- True yields an inconsistent document, Uncertain a
consistent one. False means the premises entail the NEGATION of the conclusion,
which would make premises-plus-negated-conclusion consistent; that is sound in
principle but conflates two different reasons a document can be consistent, so it
is excluded to keep the negative class clean.

Usage:
    python -m tools.prepare_folio --out validation_folio --split validation
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_UNICODE_FOL = {
    "∀": "forall ", "∃": "exists ", "→": "->", "↔": "<->",
    "∧": " and ", "∨": " or ", "¬": " not ", "⊕": " xor ", "≠": " != ",
}


def normalise_gold_fol(s: str) -> str:
    """Rewrite FOLIO's unicode logic notation into this project's ASCII syntax.

    Purely notational: quantifier and connective SYMBOLS are substituted, and
    FOLIO's `∀x (...)` becomes `forall x. (...)`. No structure is altered, so a
    mismatch after this cannot be blamed on the rewrite.
    """
    out = s
    for k, v in _UNICODE_FOL.items():
        out = out.replace(k, v)
    # "forall x (" -> "forall x. ("   and the same for exists
    out = re.sub(r"\b(forall|exists)\s+([A-Za-z]\w*)\s*\(", r"\1 \2. (", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def negate_sentence(s: str) -> str:
    """Faithful negation of an arbitrary declarative sentence.

    Deliberately syntactic rather than clever: rewriting the verb would require
    interpreting the sentence, and a wrong rewrite becomes a silent mislabel.
    """
    t = s.strip().rstrip(".").strip()
    return f"It is not the case that {t[0].lower() + t[1:]}." if t else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="validation_folio")
    ap.add_argument("--split", default="validation")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    import warnings
    warnings.filterwarnings("ignore")
    from datasets import load_dataset

    ds = load_dataset("tasksource/folio")[a.split]
    rows = list(ds)[: a.limit] if a.limit else list(ds)

    out = Path(a.out)
    (out / "docs").mkdir(parents=True, exist_ok=True)

    manifest, pairs, skipped = [], [], 0
    for i, r in enumerate(rows, 1):
        prem = str(r.get("premises") or "").strip()
        concl = str(r.get("conclusion") or "").strip()
        label = str(r.get("label") or "").strip()
        if not prem or not concl:
            skipped += 1
            continue

        # (b) translation pairs: one per premise line that has a gold formula.
        p_lines = [x.strip() for x in prem.split("\n") if x.strip()]
        g_lines = [x.strip() for x in str(r.get("premises-FOL") or "").split("\n") if x.strip()]
        if len(p_lines) == len(g_lines):
            for pl, gl in zip(p_lines, g_lines):
                pairs.append({"story_id": r.get("story_id"), "sentence": pl,
                              "gold_fol_raw": gl, "gold_fol": normalise_gold_fol(gl)})

        # (a) contradiction documents, True/Uncertain only.
        if label not in ("True", "Uncertain"):
            continue
        expect = 1 if label == "True" else 0
        doc_id = f"fo{i:04d}_{'pos' if expect else 'neg'}"
        (out / "docs" / f"{doc_id}.txt").write_text(
            f"Statement\n\n{prem}\n\n{negate_sentence(concl)}\n", encoding="utf-8")
        manifest.append({"id": doc_id, "expect_inconsistent": expect,
                         "folio_label": label, "story_id": r.get("story_id"),
                         "n_premises": len(p_lines)})

    (out / "gold.json").write_text(json.dumps({
        "source": "tasksource/folio", "split": a.split,
        "adaptation": ("premises + negated conclusion; label True -> inconsistent, "
                       "Uncertain -> consistent; False excluded"),
        "documents": manifest,
    }, indent=2), encoding="utf-8")
    (out / "translation_pairs.json").write_text(json.dumps({
        "source": "tasksource/folio", "split": a.split,
        "note": ("sentence-level gold FOL by human annotators, notation rewritten "
                 "to this project's ASCII syntax; used to score the translation "
                 "stage in isolation"),
        "pairs": pairs,
    }, indent=2), encoding="utf-8")

    pos = sum(d["expect_inconsistent"] for d in manifest)
    print(f"contradiction docs : {len(manifest)}  ({pos} inconsistent, "
          f"{len(manifest)-pos} consistent; skipped {skipped})")
    print(f"translation pairs  : {len(pairs)}  (sentence + human gold FOL)")
    print(f"  -> {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
