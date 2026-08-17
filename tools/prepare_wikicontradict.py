"""Adapt the WikiContradict benchmark into documents this pipeline can read.

Source: ibm-research/Wikipedia_contradict_benchmark (MIT licence), 253
human-annotated instances of real knowledge conflicts between Wikipedia
passages, labelled Explicit or Implicit (reasoning required).

The adaptation is one field: `merged_context` already contains the two
conflicting passages joined, which is exactly a single document that contradicts
itself. Nothing is rewritten, so the labels remain the annotators'.

What this set can and cannot measure. Every instance contains a contradiction,
so there are NO negative cases: it measures RECALL on real, externally labelled
conflicts and nothing else. Precision and false-positive rate have to come from
a set that contains clean documents -- see tools/make_validation_set.py. Reporting
recall from here as though it were accuracy would be wrong.

Usage:
    python -m tools.prepare_wikicontradict --csv /tmp/wc.csv --out validation_wc
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def _slug(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:n] or "doc"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="WikiContradict_dataset_v1_rag_qa.csv")
    ap.add_argument("--out", default="validation_wc")
    ap.add_argument("--limit", type=int, default=0, help="0 = all 253")
    a = ap.parse_args(argv)

    rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    if a.limit:
        rows = rows[: a.limit]

    out = Path(a.out)
    (out / "docs").mkdir(parents=True, exist_ok=True)

    manifest, skipped = [], 0
    for r in rows:
        ctx = (r.get("merged_context") or "").strip()
        if len(ctx) < 40:                      # nothing to reason over
            skipped += 1
            continue
        qid = r.get("question_ID", "?")
        doc_id = f"wc{int(qid):03d}_{_slug(r.get('WikipediaArticleTitle',''))}" \
            if str(qid).isdigit() else f"wc_{_slug(r.get('WikipediaArticleTitle',''))}"
        title = r.get("WikipediaArticleTitle", "").strip()
        (out / "docs" / f"{doc_id}.txt").write_text(
            f"{title}\n\n{ctx}\n", encoding="utf-8")
        manifest.append({
            "id": doc_id,
            "expect_inconsistent": 1,          # every instance is a conflict
            "contradict_type": r.get("contradictType", "").strip(),
            "same_passage": r.get("samepassage", "").strip(),
            "article": title,
            "url": r.get("url", "").strip(),
            "chars": len(ctx),
        })

    (out / "gold.json").write_text(json.dumps({
        "source": "ibm-research/Wikipedia_contradict_benchmark",
        "licence": "MIT",
        "adaptation": "merged_context used verbatim as a single document; labels unchanged",
        "measures": "RECALL only -- the benchmark contains no contradiction-free documents",
        "documents": manifest,
    }, indent=2), encoding="utf-8")

    types: dict[str, int] = {}
    for d in manifest:
        types[d["contradict_type"]] = types.get(d["contradict_type"], 0) + 1
    print(f"{len(manifest)} documents -> {out}/   (skipped {skipped} too short)")
    for k, v in sorted(types.items(), key=lambda x: -x[1]):
        print(f"   {v:4}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
