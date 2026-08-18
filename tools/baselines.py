"""Two baselines, so the system's numbers have a reference point.

Without a comparison, a recall figure floats: 80% is only meaningful against
something. These are the two alternatives a reader would reasonably propose.

  whole-doc   Give the entire document to a strong model and ask directly
              whether it contradicts itself. This is the obvious modern
              approach and the strongest competitor.

  pairwise    Ask, for every pair of sentences independently, whether those two
              contradict; flag the document if any pair does. This is what
              sentence-pair entailment (NLI) does. Each judgement sees ONLY its
              two sentences -- that isolation is the whole point, so the pairs
              are never batched into one prompt, which would let the model
              reason across them and stop being a pairwise method.

Both are scored by the same rule as the pipeline (did it flag the document?)
against the same labels, so the numbers are directly comparable.

Usage:
    python -m tools.baselines --set validation/proofwriter --mode whole-doc \
        --provider cerebras_paid --model gpt-oss-120b --workers 16
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

WHOLE_DOC_SYS = (
    "You judge whether a set of statements is logically self-contradictory.\n"
    "A contradiction means the statements cannot ALL be true at the same time, "
    "including when this only follows after several steps of reasoning.\n"
    "Answer with exactly one word: YES if they cannot all be true, NO if they can."
)

PAIR_SYS = (
    "You judge whether two statements contradict each other.\n"
    "Answer with exactly one word: YES if the two statements cannot both be true, "
    "NO otherwise."
)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def sentences(text: str) -> list[str]:
    """Split a document body into statements. Deliberately simple: these corpora
    use one short declarative per sentence."""
    body = "\n".join(text.strip().splitlines()[1:])          # drop the title line
    parts = re.split(r"(?<=[.!?])\s+", body.replace("\n", " "))
    return [p.strip() for p in parts if len(p.strip()) > 3]


def yes(s: str | None) -> bool:
    return bool(s) and s.strip().upper().lstrip("*# ").startswith("YES")


def make_client(provider: str, model: str):
    from consistency_checker.llm_client import LLMClient, LLMConfig
    from consistency_checker.providers import resolve_model_config
    cfg = resolve_model_config(provider, model)
    if cfg is None:
        raise SystemExit(f"could not resolve {provider}/{model}")
    return LLMClient(LLMConfig(overrides=cfg))


def run_doc(client, doc, docs_dir: Path, mode: str, effort: str, max_pairs: int) -> dict:
    text = (docs_dir / f"{doc['id']}.txt").read_text(encoding="utf-8")
    sents = sentences(text)
    t0 = time.time()
    calls = 0
    flagged = False
    try:
        if mode == "whole-doc":
            out = client._raw(WHOLE_DOC_SYS, text, reasoning_effort=effort)
            calls = 1
            flagged = yes(out)
        else:
            pairs = [(a, b) for i, a in enumerate(sents) for b in sents[i + 1:]]
            if max_pairs:
                pairs = pairs[:max_pairs]
            for a, b in pairs:
                out = client._raw(PAIR_SYS, f"1. {a}\n2. {b}", reasoning_effort=effort)
                calls += 1
                if yes(out):
                    flagged = True
                    break            # one contradicting pair is enough to flag
    except Exception as e:
        return {"id": doc["id"], "found": None, "error": str(e)[:120],
                "calls": calls, "wall_s": round(time.time() - t0, 1)}
    return {"id": doc["id"], "found": int(flagged), "calls": calls,
            "n_sentences": len(sents), "wall_s": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="set_dir", required=True)
    ap.add_argument("--mode", choices=["whole-doc", "pairwise"], required=True)
    ap.add_argument("--provider", default="cerebras_paid")
    ap.add_argument("--model", default="gpt-oss-120b")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-pairs", type=int, default=120,
                    help="cap pairs per document (cost control)")
    a = ap.parse_args(argv)

    sd = Path(a.set_dir)
    gold = json.loads((sd / "gold.json").read_text())
    docs = gold["documents"][: a.limit] if a.limit else gold["documents"]
    client = make_client(a.provider, a.model)

    print(f"baseline={a.mode} | {a.provider}/{a.model} | {len(docs)} docs | workers={a.workers}")
    t0 = time.time()
    recs = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(
                lambda d: run_doc(client, d, sd / "docs", a.mode, a.effort, a.max_pairs), docs), 1):
            recs.append(r)
            if i % 25 == 0 or i == len(docs):
                print(f"  {i}/{len(docs)} ({round(time.time()-t0)}s)", flush=True)

    res = {r["id"]: r for r in recs}
    tp = fp = fn = tn = err = 0
    strata: dict = {}
    for d in docs:
        r = res.get(d["id"], {})
        if r.get("found") is None:
            err += 1
            continue
        w, g = d["expect_inconsistent"], r["found"]
        if w and g: tp += 1
        elif w and not g: fn += 1
        elif not w and g: fp += 1
        else: tn += 1
        if w and "hops" in d:
            s = strata.setdefault(str(d["hops"]), {"k": 0, "n": 0})
            s["n"] += 1; s["k"] += g

    rec, fpr = wilson(tp, tp + fn), wilson(fp, fp + tn)
    pre = wilson(tp, tp + fp) if (tp + fp) else (float("nan"),) * 3
    f1 = (2 * pre[0] * rec[0] / (pre[0] + rec[0])) if (tp + fp) and (tp + fn) and (pre[0] + rec[0]) else float("nan")
    out = {
        "baseline": a.mode, "set": sd.name, "provider": a.provider, "model": a.model,
        "n_scored": tp + fp + fn + tn, "n_errors": err,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "recall": {"value": rec[0], "ci95": list(rec[1:]), "n": tp + fn},
        "false_positive_rate": {"value": fpr[0], "ci95": list(fpr[1:]), "n": fp + tn},
        "precision": {"value": pre[0], "ci95": list(pre[1:]), "n": tp + fp},
        "f1": f1,
        "recall_by_hops": {k: {"value": wilson(v["k"], v["n"])[0], "n": v["n"]}
                           for k, v in sorted(strata.items())},
        "mean_calls_per_doc": round(sum(r.get("calls", 0) for r in recs) / max(1, len(recs)), 1),
        "total_wall_s": round(time.time() - t0, 1),
    }
    dest = ROOT / "results" / f"baseline_{sd.name}_{a.mode}_{a.model.replace('/','_')}"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "metrics.json").write_text(json.dumps(out, indent=2))
    (dest / "per_doc.json").write_text(json.dumps(recs, indent=2))

    def s(d):
        return "n/a" if d["n"] == 0 or d["value"] != d["value"] else \
            f"{d['value']*100:5.1f}%  [{d['ci95'][0]*100:.1f}-{d['ci95'][1]*100:.1f}]  n={d['n']}"
    print(f"\n{'='*62}\nBASELINE {a.mode} | {sd.name} | {a.model}\n{'='*62}")
    print(f"  scored {out['n_scored']}  errors {err}")
    print(f"  recall               {s(out['recall'])}")
    print(f"  false-positive rate  {s(out['false_positive_rate'])}")
    print(f"  precision            {s(out['precision'])}")
    print(f"  F1                   {f1*100:5.1f}%" if f1 == f1 else "  F1  n/a")
    if out["recall_by_hops"]:
        print("  recall by depth:", {k: round(v["value"]*100, 1) for k, v in out["recall_by_hops"].items()})
    print(f"  calls/doc            {out['mean_calls_per_doc']}")
    print(f"\n  -> {dest/'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
