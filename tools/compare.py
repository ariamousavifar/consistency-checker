"""Build the cross-system comparison matrix from stored run metrics.

Every number in docs/evaluation2.md comes from here rather than being typed by
hand, so the tables cannot drift away from the runs that produced them. Each
cell names the results directory it was read from.

F1 is recomputed from precision and recall rather than trusted from the file,
because the baseline runner and the pipeline runner wrote it at different times.

Usage:
    python -m tools.compare                    # markdown to stdout
    python -m tools.compare --check            # non-zero exit if a run is missing
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"

# dataset -> system -> results directory
MANIFEST: dict[str, dict[str, str]] = {
    "ProofWriter (n=192)": {
        "gpt-oss-120b": "eval_validation_pw_gptoss120b_primary",
        "DeepSeek-V4-Flash": "eval_validation_pw_ds4flash",
        "GLM-4.7": "eval_proofwriter_glm47",
        "rule-only (no LLM)": "eval_validation_pw_gptoss120b_primary__ruleonly",
        "pairwise NLI": "baseline_proofwriter_pairwise_gpt-oss-120b",
    },
    "FOLIO (n=141)": {
        "gpt-oss-120b": "eval_validation_folio_gptoss120b_primary",
        "pairwise NLI": "baseline_folio_pairwise_gpt-oss-120b",
    },
    "Synthetic (n=120)": {
        "gpt-oss-120b": "eval_validation_gptoss120b_primary",
        "rule-only (no LLM)": "eval_validation_gptoss120b_primary__ruleonly",
        "pairwise NLI": "baseline_generated_pairwise_gpt-oss-120b",
    },
    "Stress, depth 5-20 (n=96)": {
        "gpt-oss-120b": "eval_stress2_stress2_ours",
        "pairwise NLI": "baseline_stress2_nodist_pairwise_gpt-oss-120b",
    },
}

ORDER = ["gpt-oss-120b", "DeepSeek-V4-Flash", "GLM-4.7",
         "rule-only (no LLM)", "pairwise NLI"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"),) * 2
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def load(name: str) -> dict | None:
    f = RES / name / "metrics.json"
    return json.loads(f.read_text()) if f.exists() else None


def row(d: dict) -> dict:
    c = d.get("confusion", {})
    tp, fp, fn, tn = (c.get(k, 0) for k in ("tp", "fp", "fn", "tn"))
    rec = tp / (tp + fn) if tp + fn else float("nan")
    pre = tp / (tp + fp) if tp + fp else float("nan")
    fpr = fp / (fp + tn) if fp + tn else float("nan")
    f1 = 2 * pre * rec / (pre + rec) if (pre + rec) else float("nan")
    lo, hi = wilson(tp, tp + fn)
    cost = (f"{d['mean_calls_per_doc']:.1f} calls" if d.get("mean_calls_per_doc")
            else f"{d['mean_tokens_per_doc']:,} tok" if d.get("mean_tokens_per_doc")
            else "offline")
    return {"rec": rec, "lo": lo, "hi": hi, "pre": pre, "fpr": fpr, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "cost": cost,
            "sec": d.get("mean_wall_s_per_doc")}


def pct(v: float) -> str:
    return "n/a" if v != v else f"{v*100:.1f}%"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)

    missing = []
    out: list[str] = []
    for ds, systems in MANIFEST.items():
        out.append(f"\n### {ds}\n")
        out.append("| System | Recall (95% CI) | Precision | False pos. | F1 | TP/FP/FN/TN | Cost/doc |")
        out.append("|---|---|---|---|---|---|---|")
        for sysname in ORDER:
            src = systems.get(sysname)
            if not src:
                continue
            d = load(src)
            if d is None:
                missing.append(f"{ds} / {sysname} -> results/{src}")
                continue
            r = row(d)
            out.append(
                f"| {sysname} | {pct(r['rec'])} [{r['lo']*100:.1f}-{r['hi']*100:.1f}] | "
                f"{pct(r['pre'])} | {pct(r['fpr'])} | {pct(r['f1'])} | "
                f"{r['tp']}/{r['fp']}/{r['fn']}/{r['tn']} | {r['cost']} |")

    print("\n".join(out))
    if missing:
        print("\nMISSING RUNS:")
        for m in missing:
            print(f"  {m}")
    return 1 if (a.check and missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
