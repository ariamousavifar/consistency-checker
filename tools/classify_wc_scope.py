"""Characterise what KIND of conflict each WikiContradict instance contains.

Purpose. Overall recall on a benchmark is only interpretable if you know what the
benchmark contains. WikiContradict was built to test retrieval-augmented systems
facing conflicting passages, so its conflicts are real-world *knowledge*
disagreements. A first-order-logic checker detects *logical* contradiction. This
script measures how much of the benchmark falls in each category, so recall can
be reported against the portion that is in scope as well as overall.

Method, and why it is defensible. Classification uses ONLY the benchmark's own
`context1` and `context2` fields via fixed, published lexical criteria. It does
NOT consult our pipeline's output. That matters: letting the system under test
decide which cases it should be excused from would be circular. Every rule below
is deterministic and auditable, and the full per-instance assignment is written
out so a reader can check any judgement by hand.

Categories are assigned in priority order, because one passage pair can carry
several signals and a single label per instance is needed for a breakdown:

  numeric_temporal   the two passages differ in a number or a year, or turn on
                     tense/date. FOL as used here has no arithmetic, ordering or
                     tense, so such a conflict cannot be represented at all.
  hedge_degree       one passage hedges what the other asserts flatly
                     ("partially", "reported to be", "mainly"). The disagreement
                     is about degree or confidence, not truth-value; both sides
                     often reduce to the SAME formula.
  entity_knowledge   near-identical wording differing in one content term, so
                     detecting the conflict requires knowing those terms are
                     incompatible (world knowledge, not derivable from the text).
  causal_temporal    the conflict is about what caused or preceded what.
  logical_candidate  contains explicit negation or quantification, i.e. the shape
                     a first-order contradiction actually takes. IN SCOPE.

Usage:
    python -m tools.classify_wc_scope --csv /tmp/wc.csv \
        --eval results/eval_validation_wc_gptoss120b_primary --out validation_wc/scope.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

HEDGE = re.compile(r"\b(partially|partly|mainly|mostly|largely|approximately|about|"
                   r"around|roughly|said to be|reported(?:ly)? to be|reportedly|"
                   r"believed to be|thought to be|generally|typically|often|some|"
                   r"several|many|may|might|possibly|likely|probable|probably|"
                   r"estimated|nearly|almost|up to|at least|more than)\b", re.I)
CAUSAL = re.compile(r"\b(caused?|causing|because|due to|led to|resulted?|"
                    r"resulting|thereby|consequently|after|before|since|"
                    r"following|subsequently|later|earlier|then)\b", re.I)
NEGQ = re.compile(r"\b(not|no|never|none|neither|nor|cannot|can't|isn't|aren't|"
                  r"wasn't|weren't|without|fails? to|all|every|any|each|only)\b", re.I)
NUM = re.compile(r"\b\d[\d,.]*\b")
WORD = re.compile(r"[a-z]{3,}")

STOP = {"the", "and", "was", "were", "are", "for", "with", "that", "this", "from",
        "has", "have", "had", "its", "his", "her", "their", "which", "been",
        "also", "but", "not", "can", "may", "one", "two", "other", "some"}


def content(s: str) -> set[str]:
    return {w for w in WORD.findall(s.lower()) if w not in STOP}


def classify(c1: str, c2: str) -> str:
    n1, n2 = set(NUM.findall(c1)), set(NUM.findall(c2))
    # A differing number/year cannot be expressed: no arithmetic or ordering here.
    if (n1 or n2) and n1 != n2:
        return "numeric_temporal"
    if HEDGE.search(c1) or HEDGE.search(c2):
        return "hedge_degree"
    w1, w2 = content(c1), content(c2)
    if w1 and w2:
        jac = len(w1 & w2) / len(w1 | w2)
        diff = (w1 ^ w2)
        # Near-identical wording differing in one or two content terms: the
        # conflict lives in world knowledge about those terms.
        if jac >= 0.5 and 0 < len(diff) <= 4:
            return "entity_knowledge"
    if CAUSAL.search(c1) or CAUSAL.search(c2):
        return "causal_temporal"
    if NEGQ.search(c1) or NEGQ.search(c2):
        return "logical_candidate"
    return "other_unexpressible"


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--eval", required=True, help="results/eval_validation_wc_... dir")
    ap.add_argument("--out", default="validation_wc/scope.json")
    a = ap.parse_args(argv)

    rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    ev = Path(a.eval)

    found: dict[str, int] = {}
    for rj in ev.glob("*/report.json"):
        try:
            d = json.loads(rj.read_text())
        except Exception:
            continue
        found[rj.parent.name] = 1 if any(
            not c.get("axioms_consistent", True) for c in d.get("clusters", [])) else 0

    per: list[dict] = []
    for r in rows:
        qid = r.get("question_ID", "")
        cat = classify(r.get("context1", ""), r.get("context2", ""))
        # match the doc id built by prepare_wikicontradict
        hit = next((v for k, v in found.items()
                    if str(qid).isdigit() and k.startswith(f"wc{int(qid):03d}_")), None)
        per.append({"question_ID": qid, "category": cat,
                    "annotated_type": r.get("contradictType", ""),
                    "detected": hit})

    cats: dict[str, dict] = {}
    for p in per:
        c = cats.setdefault(p["category"], {"n": 0, "scored": 0, "detected": 0})
        c["n"] += 1
        if p["detected"] is not None:
            c["scored"] += 1
            c["detected"] += p["detected"]

    total = len(per)
    in_scope = cats.get("logical_candidate", {"n": 0, "scored": 0, "detected": 0})
    out_n = total - in_scope["n"]

    summary = {
        "source": "ibm-research/Wikipedia_contradict_benchmark",
        "method": ("deterministic lexical criteria over the benchmark's own context1/"
                   "context2 fields; independent of the system under test"),
        "n_instances": total,
        "categories": {
            k: {"n": v["n"], "share": round(v["n"] / total, 4),
                "recall": wilson(v["detected"], v["scored"])[0],
                "recall_ci95": list(wilson(v["detected"], v["scored"])[1:]),
                "scored": v["scored"]}
            for k, v in sorted(cats.items(), key=lambda x: -x[1]["n"])},
        "in_scope_share": round(in_scope["n"] / total, 4),
        "out_of_scope_share": round(out_n / total, 4),
        "per_instance": per,
    }
    Path(a.out).write_text(json.dumps(summary, indent=2))

    print(f"\n{'='*72}\nWikiContradict composition  (n={total})\n{'='*72}")
    print(f"  {'category':22} {'n':>5} {'share':>7}   recall on that category")
    for k, v in summary["categories"].items():
        r = v["recall"]
        rs = "n/a" if r != r else f"{r*100:5.1f}%  [{v['recall_ci95'][0]*100:.0f}-{v['recall_ci95'][1]*100:.0f}]  n={v['scored']}"
        print(f"  {k:22} {v['n']:5} {v['share']*100:6.1f}%   {rs}")
    print(f"\n  expressible in this FOL fragment : {in_scope['n']}/{total} "
          f"({summary['in_scope_share']*100:.1f}%)")
    print(f"  structurally out of scope         : {out_n}/{total} "
          f"({summary['out_of_scope_share']*100:.1f}%)")
    print(f"\n  per-instance detail -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
