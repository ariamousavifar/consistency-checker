"""Stress set: does the advantage appear at greater DEPTH and LENGTH?

The hypothesis under test is that a symbolic pipeline should pull ahead of a
language model reading the whole document once the chain of inference is long and
the document is cluttered, because a solver is indifferent to both while an
attention-based reader is not.

It is designed to be able to FALSIFY that. Two factors vary independently:

  depth        5, 10, 15, 20 implication steps between the planted instance and
               the property it is finally denied. ProofWriter tops out at 5.
  distractors  0, 40, 100 additional true statements, which lengthen the document
               without changing its logical content.

Distractors are deliberately NOT off-topic filler. They reuse the same predicate
vocabulary applied to OTHER entities, so they cannot be filtered by surface
similarity and a reader has to actually track which entity is which. Off-topic
padding would make the task artificially easy and the result meaningless.

Both systems receive byte-identical documents, and the labels follow from
construction. Prediction recorded before running: the language model degrades
faster than the pipeline as depth and clutter rise. If it does not, the
hypothesis is wrong and the result stands as written.

Usage:
    python -m tools.make_stress_set --out validation/stress --seed 20260817
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

# 21 status predicates: enough for a 20-step chain, and they read as ordinary
# administrative language rather than as c0/c1/c2 placeholders.
LADDER = ["registered", "catalogued", "audited", "certified", "bonded",
          "insured", "licensed", "approved", "verified", "inspected",
          "cleared", "endorsed", "ratified", "authorised", "accredited",
          "chartered", "sanctioned", "validated", "warranted", "attested",
          "notarised"]
FINAL_PROP = "transferable"

ENTITIES = ["consignment", "parcel", "shipment", "container", "crate", "pallet",
            "lot", "batch", "unit", "carton", "bundle", "case"]
TAGS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
        "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
        "oscar", "papa", "quebec", "romeo", "sierra", "tango"]

RULE = ["Every {a} item is a {b} item.",
        "All {a} items are {b} items.",
        "Any {a} item is also a {b} item.",
        "If an item is {a} then it is {b}."]


def _unique_names(rng: random.Random, k: int) -> list[str]:
    """Globally distinct entity names. Every name carries its own number, so no
    two objects can share a surface form and a naming collision cannot be
    mistaken for a logical conflict."""
    pool = [f"{e} {t}-{i:02d}" for i, (e, t) in enumerate(
        ((rng.choice(ENTITIES), rng.choice(TAGS)) for _ in range(k * 3)), start=1)]
    rng.shuffle(pool)
    return pool[:k]


def build(rng: random.Random, depth: int, n_dist: int, positive: bool, doc_id: str) -> dict:
    chain = LADDER[: depth + 1]
    names = _unique_names(rng, n_dist + 2)
    subject = names[0]

    lines = [RULE[rng.randrange(len(RULE))].format(a=chain[i], b=chain[i + 1])
             for i in range(depth)]
    lines.append(f"Every {chain[depth]} item is {FINAL_PROP}.")
    lines.append(f"{subject.capitalize()} is {chain[0]}.")
    if positive:
        lines.append(f"{subject.capitalize()} is not {FINAL_PROP}.")
    else:
        other = names[1]
        lines.append(f"{other.capitalize()} is not {FINAL_PROP}.")

    # Distractors: same vocabulary, different entities. Interleaved throughout so
    # the relevant statements are not conveniently adjacent.
    distract = []
    for j in range(n_dist):
        e = names[j + 2]
        p = rng.choice(LADDER)
        distract.append(rng.choice([
            f"{e.capitalize()} is {p}.",
            f"{e.capitalize()} was recorded as {p} at the depot.",
            f"The register lists {e} as {p}.",
        ]))
    body = lines + distract
    rng.shuffle(body)                      # relevant statements scattered

    text = ("Depot register\n\n" + " ".join(body) + "\n")
    return {"id": doc_id, "expect_inconsistent": int(positive),
            "hops": depth, "distractors": n_dist,
            "n_statements": len(body), "chars": len(text), "text": text}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="validation/stress")
    ap.add_argument("--seed", type=int, default=20260817)
    ap.add_argument("--per-cell", type=int, default=4)
    a = ap.parse_args(argv)

    rng = random.Random(a.seed)
    out = Path(a.out)
    (out / "docs").mkdir(parents=True, exist_ok=True)

    manifest = []
    i = 0
    for depth in (5, 10, 15, 20):
        for n_dist in (0, 40, 100):
            for positive in (True, False):
                for _ in range(a.per_cell):
                    i += 1
                    did = f"st{i:03d}_d{depth}_x{n_dist}_{'pos' if positive else 'neg'}"
                    d = build(rng, depth, n_dist, positive, did)
                    (out / "docs" / f"{did}.txt").write_text(d.pop("text"), encoding="utf-8")
                    manifest.append(d)

    (out / "gold.json").write_text(json.dumps({
        "seed": a.seed,
        "generator": "tools/make_stress_set.py",
        "hypothesis": ("a symbolic pipeline should degrade more slowly than a "
                       "whole-document language model as inference depth and "
                       "document clutter increase"),
        "prediction_recorded_before_running": True,
        "factors": {"depth": [5, 10, 15, 20], "distractors": [0, 40, 100]},
        "documents": manifest,
    }, indent=2), encoding="utf-8")

    pos = sum(d["expect_inconsistent"] for d in manifest)
    chars = [d["chars"] for d in manifest]
    print(f"{len(manifest)} documents -> {out}/  ({pos} inconsistent, {len(manifest)-pos} clean)")
    print(f"  depths {sorted({d['hops'] for d in manifest})}  "
          f"distractors {sorted({d['distractors'] for d in manifest})}")
    print(f"  length: min {min(chars)} / median {sorted(chars)[len(chars)//2]} / max {max(chars)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
