"""Adapt ProofWriter into a contradiction-detection validation set.

Why this set. It answers the objection that a system's authors also wrote its
test. The theories, the labels and the reasoning-depth stratification all come
from an external source (tasksource/proofwriter), and the adaptation rule below
is fixed before any result is seen.

The adaptation, and why it is sound. ProofWriter is an entailment task: does a
theory entail a question? Entailment and inconsistency are duals, so a label
converts to a contradiction label without touching the text:

    answer = True     theory entails Q, therefore theory + NOT Q is INCONSISTENT
    answer = Unknown  theory entails neither Q nor NOT Q, therefore
                      theory + NOT Q is CONSISTENT

`answer = False` is skipped. Under ProofWriter's open-world configurations False
can mean "NOT Q is provable", which would make theory + NOT Q consistent, but
under closed-world configurations it means "Q is not provable", which is a
different claim. Rather than encode that distinction we use only the two labels
whose reading is unambiguous either way.

Negation is produced by a small deterministic transform on ProofWriter's fixed
sentence shapes ("X is Y." / "X is not Y."), never by a model, so no judgement
enters the label.

`QDep` -- the number of inference steps the original proof required -- is carried
through as the stratifier, giving an externally defined equivalent of hop depth.

Usage:
    python -m tools.prepare_proofwriter --out validation_pw --n 200 --seed 20260816
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path


def negate(sentence: str) -> str | None:
    """Deterministically negate a ProofWriter question.

    Their questions use a fixed copular form, so this is a mechanical
    transformation rather than an interpretation. Anything not matching the
    expected shape returns None and the instance is skipped, so a malformed
    negation can never become a silent mislabel.
    """
    s = sentence.strip().rstrip(".").strip()
    if not s:
        return None
    m = re.match(r"^(.*?)\s+is\s+not\s+(.*)$", s, re.I)
    if m:                                    # "X is not Y" -> "X is Y"
        return f"{m.group(1)} is {m.group(2)}."
    m = re.match(r"^(.*?)\s+is\s+(.*)$", s, re.I)
    if m:                                    # "X is Y" -> "X is not Y"
        return f"{m.group(1)} is not {m.group(2)}."
    m = re.match(r"^(.*?)\s+(does not|doesn't)\s+(.*)$", s, re.I)
    if m:
        return f"{m.group(1)} {m.group(3)}."
    m = re.match(r"^(.*?)\s+(sees|likes|visits|chases|eats|needs)\s+(.*)$", s, re.I)
    if m:                                    # simple transitive present tense
        return f"{m.group(1)} does not {m.group(2).rstrip('s')} {m.group(3)}."
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="validation_pw")
    ap.add_argument("--n", type=int, default=200, help="target document count")
    ap.add_argument("--seed", type=int, default=20260816)
    ap.add_argument("--max-depth", type=int, default=5)
    ap.add_argument("--split", default="validation")
    a = ap.parse_args(argv)

    import warnings
    warnings.filterwarnings("ignore")
    from datasets import load_dataset

    ds = load_dataset("tasksource/proofwriter")[a.split]
    rng = random.Random(a.seed)

    # Balanced by construction across depth and polarity, so neither the recall
    # curve nor the false-positive rate is dominated by one cell.
    per_cell = max(1, a.n // (2 * (a.max_depth + 1)))
    want: dict[tuple, int] = {(d, lab): per_cell
                              for d in range(a.max_depth + 1)
                              for lab in ("True", "Unknown")}

    idx = list(range(len(ds)))
    rng.shuffle(idx)

    out = Path(a.out)
    (out / "docs").mkdir(parents=True, exist_ok=True)
    manifest, skipped = [], 0

    for i in idx:
        if not any(v > 0 for v in want.values()):
            break
        r = ds[i]
        lab = str(r.get("answer"))
        try:
            dep = int(r.get("QDep") or 0)
        except (TypeError, ValueError):
            continue
        if lab not in ("True", "Unknown") or dep > a.max_depth:
            continue
        if want.get((dep, lab), 0) <= 0:
            continue

        neg = negate(str(r.get("question") or ""))
        theory = str(r.get("theory") or "").strip()
        if not neg or len(theory) < 20:
            skipped += 1
            continue

        # theory + NOT Q. Inconsistent iff the theory entailed Q.
        expect = 1 if lab == "True" else 0
        doc_id = f"pw{len(manifest)+1:03d}_d{dep}_{'pos' if expect else 'neg'}"
        (out / "docs" / f"{doc_id}.txt").write_text(
            f"Report\n\n{theory}\n\n{neg}\n", encoding="utf-8")

        manifest.append({
            "id": doc_id,
            "expect_inconsistent": expect,
            "hops": dep,                      # QDep, externally defined
            "pw_id": r.get("id"),
            "pw_answer": lab,
            "n_facts": r.get("NFact"),
            "n_rules": r.get("NRule"),
            "config": r.get("config"),
            "added_negation": neg,
        })
        want[(dep, lab)] -= 1

    (out / "gold.json").write_text(json.dumps({
        "source": "tasksource/proofwriter",
        "split": a.split,
        "seed": a.seed,
        "adaptation": ("entailment converted to consistency by appending the "
                       "deterministically negated question: answer=True -> "
                       "inconsistent, answer=Unknown -> consistent; answer=False "
                       "excluded as its reading depends on the open/closed-world "
                       "configuration"),
        "stratifier": "hops = ProofWriter QDep, the inference depth of the original proof",
        "documents": manifest,
    }, indent=2), encoding="utf-8")

    pos = sum(d["expect_inconsistent"] for d in manifest)
    dist: dict[int, int] = {}
    for d in manifest:
        dist[d["hops"]] = dist.get(d["hops"], 0) + 1
    print(f"{len(manifest)} documents -> {out}/  ({pos} inconsistent, "
          f"{len(manifest)-pos} consistent; skipped {skipped} unnegatable)")
    print("  depth distribution:", dict(sorted(dist.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
