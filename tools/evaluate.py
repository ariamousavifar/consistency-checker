"""Run the pipeline over a validation set and report quantitative results.

Reports proportions with Wilson 95% confidence intervals. The intervals are not
decoration: on 60 documents a single flip moves a rate by 1.7 points, and any
reader entitled to ask "is that difference real?" needs the interval to answer.

Metrics, and what each is for:

  recall            of documents that DO contain a contradiction, the fraction
                    where one was found. Bounded by translation coverage, not by
                    the solver.
  false-positive    of documents that do NOT, the fraction wrongly flagged. This
                    is the number the design optimises for: a checker that
                    invents contradictions is unusable.
  precision         flagged documents that were genuinely contradictory.
  F1                harmonic mean, for a single headline figure.
  recall by hop     recall split by how many implication steps separate the
                    conflict. The axis on which sentence-pair methods fail by
                    construction: at hop >= 2 no PAIR of sentences conflicts.
  quarantine rate   statements the gate refused to translate. A coverage
                    diagnostic, not an error -- it explains recall.

Usage:
    python -m tools.evaluate --set validation --provider openrouter \
        --model deepseek/deepseek-v4-flash-0731 --workers 4
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Point estimate and Wilson score interval. Wilson rather than normal
    approximation because rates here sit near 0 or 1, where the normal interval
    produces bounds outside [0, 1]."""
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, centre - half), min(1.0, centre + half)


def run_one(doc: dict, docs_dir: Path, out_root: Path, args) -> dict:
    out_dir = out_root / doc["id"]
    done = out_dir / "_done.json"
    if done.exists():                                  # resume; never redo work
        return json.loads(done.read_text())

    cmd = [PY, "-m", "consistency_checker.main",
           "--file", str(docs_dir / f"{doc['id']}.txt"), "--out", str(out_dir),
           "--provider", args.provider, "--model", args.model,
           "--seed", str(args.seed)]
    if args.allow_relations:
        cmd.append("--allow-relations")
    if args.allow_conditionals:
        cmd.append("--allow-conditionals")
    if args.no_chunk:
        cmd.append("--no-chunk")
    env = dict(os.environ)
    env.update({"LLM_EXTRACTION_EFFORT": args.effort,
                "LLM_TRANSLATION_EFFORT": args.effort,
                "LLM_MAX_TOKENS": str(args.max_tokens)})

    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rc, timed_out = None, False
    with open(out_dir / "run.log", "w") as lf:
        try:
            rc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=lf,
                                stderr=subprocess.STDOUT, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            timed_out = True

    rec = {"id": doc["id"], "rc": rc, "timed_out": timed_out,
           "wall_s": round(time.time() - t0, 1),
           "found": None, "n_statements": None, "n_quarantined": None,
           "tokens": None}
    rj = out_dir / "report.json"
    if rj.exists():
        try:
            d = json.loads(rj.read_text())
            props = d.get("propositions", [])
            rec["found"] = 1 if any(
                not c.get("axioms_consistent", True) for c in d.get("clusters", [])) else 0
            rec["n_statements"] = len(props)
            rec["n_quarantined"] = sum(
                1 for p in props if p.get("status") in ("quarantined", "ambiguous"))
            rec["tokens"] = (d.get("usage") or {}).get("total_tokens")
        except Exception as e:
            rec["parse_error"] = str(e)[:120]
    if rc == 0 and not timed_out:
        done.write_text(json.dumps(rec, indent=2))
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="set_dir", required=True,
                    help="directory containing gold.json and docs/")
    ap.add_argument("--provider", default="openrouter")
    ap.add_argument("--model", default="deepseek/deepseek-v4-flash-0731")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--effort", default="off")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--allow-relations", action="store_true")
    ap.add_argument("--allow-conditionals", action="store_true")
    ap.add_argument("--no-chunk", action="store_true",
                    help="single-pass extraction; chunking existed for a 5 RPM free tier")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="")
    a = ap.parse_args(argv)

    sd = Path(a.set_dir)
    gold = json.loads((sd / "gold.json").read_text())
    docs = gold["documents"][: a.limit] if a.limit else gold["documents"]

    tag = a.tag or f"{a.model.replace('/', '_')}_{a.effort}"
    out_root = ROOT / "results" / f"eval_{sd.name}_{tag}"
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"evaluating {len(docs)} docs | {a.provider}/{a.model} | reasoning={a.effort} "
          f"| workers={a.workers}\n  -> {out_root}")
    t0 = time.time()
    recs: list[dict] = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(lambda d: run_one(d, sd / "docs", out_root, a), docs), 1):
            recs.append(r)
            if i % 20 == 0 or i == len(docs):
                print(f"  {i}/{len(docs)} ({round(time.time()-t0)}s)", flush=True)

    by_id = {d["id"]: d for d in docs}
    res = {r["id"]: r for r in recs}

    tp = fp = fn = tn = errors = 0
    for d in docs:
        r = res.get(d["id"], {})
        if r.get("found") is None:
            errors += 1
            continue
        want, got = d["expect_inconsistent"], r["found"]
        if want and got: tp += 1
        elif want and not got: fn += 1
        elif not want and got: fp += 1
        else: tn += 1

    scored = tp + fp + fn + tn
    rec_p, rec_lo, rec_hi = wilson(tp, tp + fn)
    fpr_p, fpr_lo, fpr_hi = wilson(fp, fp + tn)
    pre_p, pre_lo, pre_hi = wilson(tp, tp + fp) if (tp + fp) else (float("nan"),) * 3
    f1 = (2 * pre_p * rec_p / (pre_p + rec_p)) if (tp + fp) and (tp + fn) and (pre_p + rec_p) else float("nan")

    # breakdown by whichever stratifier the set provides
    strat_key = "hops" if "hops" in docs[0] else ("contradict_type" if "contradict_type" in docs[0] else None)
    strata: dict = {}
    if strat_key:
        for d in docs:
            r = res.get(d["id"], {})
            if r.get("found") is None or not d["expect_inconsistent"]:
                continue
            s = strata.setdefault(str(d[strat_key]), {"k": 0, "n": 0})
            s["n"] += 1
            s["k"] += r["found"]

    qs = [r["n_quarantined"] / r["n_statements"] for r in recs
          if r.get("n_statements")]
    toks = [r["tokens"] for r in recs if r.get("tokens")]
    walls = [r["wall_s"] for r in recs if r.get("wall_s")]

    metrics = {
        "set": sd.name, "provider": a.provider, "model": a.model,
        "reasoning": a.effort, "seed": a.seed, "allow_relations": a.allow_relations,
        "allow_conditionals": a.allow_conditionals,
        "n_documents": len(docs), "n_scored": scored, "n_errors": errors,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "recall": {"value": rec_p, "ci95": [rec_lo, rec_hi], "n": tp + fn},
        "false_positive_rate": {"value": fpr_p, "ci95": [fpr_lo, fpr_hi], "n": fp + tn},
        "precision": {"value": pre_p, "ci95": [pre_lo, pre_hi], "n": tp + fp},
        "f1": f1,
        f"recall_by_{strat_key}": {
            k: {"value": wilson(v["k"], v["n"])[0],
                "ci95": list(wilson(v["k"], v["n"])[1:]), "n": v["n"]}
            for k, v in sorted(strata.items())} if strat_key else {},
        "mean_quarantine_rate": round(sum(qs) / len(qs), 4) if qs else None,
        "mean_tokens_per_doc": round(sum(toks) / len(toks)) if toks else None,
        "mean_wall_s_per_doc": round(sum(walls) / len(walls), 1) if walls else None,
        "total_wall_s": round(time.time() - t0, 1),
    }
    (out_root / "metrics.json").write_text(json.dumps(metrics, indent=2))

    def pct(d):
        return "  n/a" if d["n"] == 0 or d["value"] != d["value"] else \
            f"{d['value']*100:5.1f}%  [{d['ci95'][0]*100:.1f}-{d['ci95'][1]*100:.1f}]  n={d['n']}"

    print(f"\n{'='*66}\n{sd.name} | {a.model} | reasoning={a.effort}\n{'='*66}")
    print(f"  documents scored     {scored}/{len(docs)}   (errors: {errors})")
    print(f"  recall               {pct(metrics['recall'])}")
    print(f"  false-positive rate  {pct(metrics['false_positive_rate'])}")
    print(f"  precision            {pct(metrics['precision'])}")
    print(f"  F1                   {metrics['f1']*100:5.1f}%" if metrics['f1'] == metrics['f1'] else "  F1                     n/a")
    if strat_key:
        print(f"  recall by {strat_key}:")
        for k, v in metrics[f"recall_by_{strat_key}"].items():
            print(f"     {k:34} {v['value']*100:5.1f}%  n={v['n']}")
    print(f"  mean quarantine rate {metrics['mean_quarantine_rate']}")
    print(f"  mean tokens/doc      {metrics['mean_tokens_per_doc']}")
    print(f"  mean wall s/doc      {metrics['mean_wall_s_per_doc']}")
    print(f"\n  metrics -> {out_root/'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
