"""Render the evaluation charts as standalone SVG.

Hand-written SVG rather than a plotting library: it adds no dependency, the
output is small and diffable, and GitHub renders it inline. Colours are chosen to
stay legible on both light and dark backgrounds, and every chart paints its own
background so it never inherits the host page's.

Every figure is drawn from the measured numbers recorded below, each annotated
with the results directory it came from, so any chart traces back to its run.

Usage:  python -m tools.make_charts --out docs/assets
"""
from __future__ import annotations

import argparse
from pathlib import Path

BG, FG, MUTED, GRID = "#ffffff", "#1f2328", "#57606a", "#d8dee4"
SERIES = {"gpt-oss-120b": "#0969da", "DeepSeek-V4-Flash": "#1a7f37",
          "GLM-4.7": "#9a6700", "rule-only (no LLM)": "#8250df",
          "pairwise NLI": "#cf222e"}
OURS, NLI, WARN = "#0969da", "#cf222e", "#9a6700"

# --- measured; source directory in results/ noted beside each ---------------
# eval_validation_pw_* / eval_proofwriter_glm47 / baseline_proofwriter_pairwise_*
DEPTH = {
    "gpt-oss-120b":       {0: 93.8, 1: 93.8, 2: 81.2, 3: 56.2, 4: 75.0, 5: 81.2},
    "DeepSeek-V4-Flash":  {0: 81.2, 1: 87.5, 2: 75.0, 3: 62.5, 4: 56.2, 5: 62.5},
    "GLM-4.7":            {0: 93.8, 1: 93.8, 2: 56.2, 3: 43.8, 4: 31.2, 5: 62.5},
    "rule-only (no LLM)": {0: 43.8, 1: 31.2, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0},
    "pairwise NLI":       {0: 100.0, 1: 6.2, 2: 18.8, 3: 12.5, 4: 12.5, 5: 31.2},
}
DASHED = {"rule-only (no LLM)", "pairwise NLI"}

# eval_stress2_* / baseline_stress2_nodist_pairwise_*
STRESS = {"this system": {5: 41.7, 10: 50.0, 15: 50.0, 20: 50.0},
          "pairwise NLI": {5: 25.0, 10: 0.0, 15: 0.0, 20: 0.0}}

# dataset, n, our recall, our FP, NLI recall, NLI FP
DATASETS = [("ProofWriter", 192, 80.2, 2.1, 30.2, 13.5),
            ("FOLIO", 141, 50.0, 4.3, 45.8, 18.8),
            ("Synthetic", 120, 100.0, 0.0, 40.0, 0.0),
            ("Stress (depth 5-20)", 96, 47.9, 10.4, 6.2, 0.0)]

# model, recall, precision, FP, tokens/doc, s/doc
MODELS = [("gpt-oss-120b", 80.2, 97.5, 2.1, 5168, 6.9),
          ("DeepSeek-V4-Flash", 70.8, 98.6, 1.0, 4924, 59.1),
          ("GLM-4.7", 63.5, 96.8, 2.1, 18381, 31.6)]


def _hdr(w, h, title, sub=""):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
         f'viewBox="0 0 {w} {h}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
         f'<rect width="{w}" height="{h}" fill="{BG}"/>',
         f'<text x="24" y="30" font-size="16" font-weight="600" fill="{FG}">{title}</text>']
    if sub:
        s.append(f'<text x="24" y="50" font-size="12" fill="{MUTED}">{sub}</text>')
    return s


def _yaxis(s, L, T, pw, ph):
    for i in range(0, 101, 25):
        y = T + ph - ph * i / 100
        s.append(f'<line x1="{L}" y1="{y}" x2="{L+pw}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{L-10}" y="{y+4}" font-size="11" fill="{MUTED}" text-anchor="end">{i}%</text>')


def depth_chart(path: Path):
    """Recall against inference depth -- the central result."""
    W, H, L, R, T, B = 760, 420, 60, 216, 74, 82
    pw, ph = W - L - R, H - T - B
    keys = sorted(DEPTH["gpt-oss-120b"])
    s = _hdr(W, H, "Recall vs. reasoning depth",
             "ProofWriter: 192 held-out documents, externally authored")
    _yaxis(s, L, T, pw, ph)
    for i, k in enumerate(keys):
        x = L + pw * i / (len(keys) - 1)
        s.append(f'<text x="{x:.0f}" y="{T+ph+20}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="middle">{k}</text>')
    s.append(f'<text x="{L+pw/2:.0f}" y="{T+ph+40}" font-size="11.5" fill="{MUTED}" '
             f'text-anchor="middle">inference steps separating the conflicting statements</text>')

    ends = []
    for name, colour in SERIES.items():
        xs = [L + pw * i / (len(keys) - 1) for i in range(len(keys))]
        pts = [(x, T + ph - ph * DEPTH[name][k] / 100) for x, k in zip(xs, keys)]
        dash = ' stroke-dasharray="5,4"' if name in DASHED else ""
        s.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" '
                 f'fill="none" stroke="{colour}" stroke-width="2.5"{dash}/>')
        for x, y in pts:
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{colour}"/>')
        ends.append([pts[-1][1], name, colour])

    ends.sort()                                    # spread colliding end labels
    for i, e in enumerate(ends):
        if i and e[0] - ends[i - 1][0] < 15:
            e[0] = ends[i - 1][0] + 15
        s.append(f'<text x="{L+pw+12:.0f}" y="{e[0]+4:.0f}" font-size="11.5" '
                 f'font-weight="600" fill="{e[2]}">{e[1]}</text>')
    s.append(f'<text x="{L}" y="{H-16}" font-size="11" fill="{MUTED}">'
             f'beyond one step no PAIR of sentences conflicts; the baseline’s residual score '
             f'tracks its 13.5% false-positive rate, not found chains</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def stress_chart(path: Path):
    """The same axis pushed past what the public corpora reach."""
    W, H, L, R, T, B = 760, 360, 60, 196, 74, 56
    pw, ph = W - L - R, H - T - B
    keys = sorted(STRESS["this system"])
    s = _hdr(W, H, "Deeper chains: 5 to 20 inference steps",
             "purpose-built stress set, 96 documents, up to 100 irrelevant statements")
    _yaxis(s, L, T, pw, ph)
    for i, k in enumerate(keys):
        x = L + pw * i / (len(keys) - 1)
        s.append(f'<text x="{x:.0f}" y="{T+ph+20}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="middle">{k}</text>')
    s.append(f'<text x="{L+pw/2:.0f}" y="{H-14}" font-size="11.5" fill="{MUTED}" '
             f'text-anchor="middle">inference steps</text>')
    for name, colour in (("this system", OURS), ("pairwise NLI", NLI)):
        xs = [L + pw * i / (len(keys) - 1) for i in range(len(keys))]
        pts = [(x, T + ph - ph * STRESS[name][k] / 100) for x, k in zip(xs, keys)]
        dash = ' stroke-dasharray="5,4"' if name == "pairwise NLI" else ""
        s.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" '
                 f'fill="none" stroke="{colour}" stroke-width="2.5"{dash}/>')
        for x, y in pts:
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>')
        s.append(f'<text x="{pts[-1][0]+12:.0f}" y="{pts[-1][1]+4:.0f}" font-size="12.5" '
                 f'font-weight="600" fill="{colour}">{name}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def dataset_chart(path: Path):
    """Recall and false positives on every corpus, against the NLI baseline."""
    W, H, L, R, T, B = 760, 400, 60, 40, 74, 96
    pw, ph = W - L - R, H - T - B
    s = _hdr(W, H, "Results by held-out dataset",
             "this system against sentence-pair entailment, identical documents and scoring")
    _yaxis(s, L, T, pw, ph)
    # colour encodes the system, opacity the metric, so both read at a glance
    bars = [(2, OURS, 1.0, "recall, this system"), (4, NLI, 1.0, "recall, pairwise NLI"),
            (3, OURS, 0.40, "false positives, this system"),
            (5, NLI, 0.40, "false positives, pairwise NLI")]
    slot = pw / len(DATASETS)
    for i, d in enumerate(DATASETS):
        cx = L + slot * (i + 0.5)
        for j, (idx, col, op, _) in enumerate(bars):
            val = d[idx]
            bw = 30
            x = cx - (len(bars) * bw + (len(bars) - 1) * 6) / 2 + j * (bw + 6)
            bh = ph * val / 100
            s.append(f'<rect x="{x:.0f}" y="{T+ph-bh:.0f}" width="{bw}" height="{bh:.0f}" '
                     f'fill="{col}" rx="2" opacity="{op}"/>')
            s.append(f'<text x="{x+bw/2:.0f}" y="{T+ph-bh-7:.0f}" font-size="10" '
                     f'font-weight="600" fill="{col}" text-anchor="middle">{val:.1f}</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+22}" font-size="12" fill="{FG}" '
                 f'text-anchor="middle">{d[0]}</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+38}" font-size="10.5" fill="{MUTED}" '
                 f'text-anchor="middle">n={d[1]}</text>')
    for j, (_, col, op, lab) in enumerate(bars):
        x = L + (j % 2) * 330
        y = H - 34 + (j // 2) * 16
        s.append(f'<rect x="{x}" y="{y}" width="11" height="11" fill="{col}" rx="2" opacity="{op}"/>')
        s.append(f'<text x="{x+17}" y="{y+10}" font-size="11" fill="{MUTED}">{lab}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def model_chart(path: Path):
    """Which translation model, and what it costs."""
    W, H, L, R, T, B = 760, 400, 60, 40, 74, 92
    pw, ph = W - L - R, H - T - B
    s = _hdr(W, H, "Translation model comparison",
             "ProofWriter, 192 documents, same seed and settings")
    _yaxis(s, L, T, pw, ph)
    bars = [("recall", 1, OURS), ("precision", 2, "#1a7f37"), ("false pos.", 3, WARN)]
    slot = pw / len(MODELS)
    for i, m in enumerate(MODELS):
        cx = L + slot * (i + 0.5)
        for j, (lab, idx, col) in enumerate(bars):
            val = m[idx]
            bw = 44
            x = cx - (len(bars) * bw + (len(bars) - 1) * 8) / 2 + j * (bw + 8)
            bh = ph * val / 100
            s.append(f'<rect x="{x:.0f}" y="{T+ph-bh:.0f}" width="{bw}" height="{bh:.0f}" '
                     f'fill="{col}" rx="2"/>')
            s.append(f'<text x="{x+bw/2:.0f}" y="{T+ph-bh-7:.0f}" font-size="11" '
                     f'font-weight="600" fill="{col}" text-anchor="middle">{val:.1f}</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+22}" font-size="12.5" font-weight="600" '
                 f'fill="{FG}" text-anchor="middle">{m[0]}</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+40}" font-size="10.5" fill="{MUTED}" '
                 f'text-anchor="middle">{m[4]:,} tokens/doc · {m[5]}s</text>')
    for j, (lab, _, col) in enumerate(bars):
        x = L + j * 160
        s.append(f'<rect x="{x}" y="{H-26}" width="11" height="11" fill="{col}" rx="2"/>')
        s.append(f'<text x="{x+17}" y="{H-16}" font-size="11.5" fill="{MUTED}">{lab}</text>')
    s.append(f'<text x="{L}" y="{T+ph+62}" font-size="11" fill="{MUTED}">'
             f'confidence intervals overlap; read the ordering as suggestive, not established</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    depth_chart(out / "recall-by-depth.svg")
    stress_chart(out / "stress-depth.svg")
    dataset_chart(out / "dataset-results.svg")
    model_chart(out / "model-comparison.svg")
    for f in sorted(out.glob("*.svg")):
        print(f"  {f}  ({f.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
