"""Render the evaluation charts as standalone SVG.

Hand-written SVG rather than a plotting library: it adds no dependency, the
output is small and diffable, and GitHub renders it inline. Colours are chosen to
stay legible on both light and dark backgrounds, and every chart paints its own
background so it never inherits the host page's.

Figures are drawn from the measured numbers recorded below, each annotated with
the results directory it came from so any figure can be traced back to the run
that produced it.

Usage:  python -m tools.make_charts --out docs/assets
"""
from __future__ import annotations

import argparse
from pathlib import Path

BG, FG, MUTED, GRID = "#ffffff", "#1f2328", "#57606a", "#d8dee4"
OURS, NLI, WARN = "#0969da", "#cf222e", "#9a6700"

# results/eval_proofwriter_gpt-oss-120b_off/metrics.json
PW_OURS = {0: 93.8, 1: 93.8, 2: 81.2, 3: 56.2, 4: 75.0, 5: 81.2}
# results/baseline_proofwriter_pairwise_gpt-oss-120b/metrics.json
PW_NLI = {0: 92.3, 1: 12.5, 2: 16.7, 3: 0.0, 4: 0.0, 5: 0.0}

# results/eval_stress2_* and results/baseline_stress2_nodist_pairwise_*
ST_OURS = {5: 41.7, 10: 50.0, 15: 50.0, 20: 50.0}
ST_NLI = {5: 25.0, 10: 0.0, 15: 0.0, 20: 0.0}

# recall, false-positive rate, n
DATASETS = [("ProofWriter", 80.2, 2.1, 192), ("FOLIO", 50.0, 4.3, 141),
            ("Synthetic", 100.0, 0.0, 120), ("Stress (depth 5-20)", 47.9, 10.4, 96)]


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
        s.append(f'<text x="{L-10}" y="{y+4}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="end">{i}%</text>')


def _line(s, series, colour, label, keys, L, T, pw, ph):
    xs = [L + pw * i / max(1, len(keys) - 1) for i in range(len(keys))]
    pts = [(x, T + ph - ph * series[k] / 100) for x, k in zip(xs, keys)]
    s.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" '
             f'fill="none" stroke="{colour}" stroke-width="2.5"/>')
    for x, y in pts:
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>')
    return pts[-1]


def depth_chart(path: Path):
    """Recall against the number of inference steps -- the central result."""
    W, H, L, R, T, B = 720, 400, 60, 210, 74, 56
    pw, ph = W - L - R, H - T - B
    keys = sorted(PW_OURS)
    s = _hdr(W, H, "Recall vs. number of inference steps",
             "ProofWriter: 192 held-out documents, externally authored")
    _yaxis(s, L, T, pw, ph)
    for i, k in enumerate(keys):
        x = L + pw * i / (len(keys) - 1)
        s.append(f'<text x="{x:.0f}" y="{T+ph+20}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="middle">{k}</text>')
    s.append(f'<text x="{L+pw/2:.0f}" y="{H-14}" font-size="11.5" fill="{MUTED}" '
             f'text-anchor="middle">inference steps separating the conflicting statements</text>')

    e1 = _line(s, PW_OURS, OURS, "ours", keys, L, T, pw, ph)
    e2 = _line(s, PW_NLI, NLI, "nli", keys, L, T, pw, ph)
    for (x, y), lab, col in ((e1, "This system", OURS), (e2, "Sentence-pair (NLI)", NLI)):
        s.append(f'<text x="{x+12:.0f}" y="{y+4:.0f}" font-size="12.5" font-weight="600" '
                 f'fill="{col}">{lab}</text>')
    s.append(f'<text x="{L+pw*0.30:.0f}" y="{T+ph-16}" font-size="11.5" fill="{MUTED}">'
             f'no single pair of sentences conflicts beyond step 1</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def stress_chart(path: Path):
    """The same axis pushed far past what the public corpora reach."""
    W, H, L, R, T, B = 720, 360, 60, 210, 74, 56
    pw, ph = W - L - R, H - T - B
    keys = sorted(ST_OURS)
    s = _hdr(W, H, "Deeper chains: 5 to 20 inference steps",
             "Purpose-built stress set, 96 documents, up to 100 irrelevant statements")
    _yaxis(s, L, T, pw, ph)
    for i, k in enumerate(keys):
        x = L + pw * i / (len(keys) - 1)
        s.append(f'<text x="{x:.0f}" y="{T+ph+20}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="middle">{k}</text>')
    s.append(f'<text x="{L+pw/2:.0f}" y="{H-14}" font-size="11.5" fill="{MUTED}" '
             f'text-anchor="middle">inference steps</text>')
    e1 = _line(s, ST_OURS, OURS, "ours", keys, L, T, pw, ph)
    e2 = _line(s, ST_NLI, NLI, "nli", keys, L, T, pw, ph)
    for (x, y), lab, col in ((e1, "This system", OURS), (e2, "Sentence-pair (NLI)", NLI)):
        s.append(f'<text x="{x+12:.0f}" y="{y+4:.0f}" font-size="12.5" font-weight="600" '
                 f'fill="{col}">{lab}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def dataset_chart(path: Path):
    """Recall and false-positive rate side by side on every corpus."""
    W, H, L, R, T, B = 720, 360, 60, 40, 74, 74
    pw, ph = W - L - R, H - T - B
    s = _hdr(W, H, "Results across four corpora",
             "recall and false-positive rate; three externally sourced, one purpose-built")
    _yaxis(s, L, T, pw, ph)
    n = len(DATASETS)
    slot = pw / n
    for i, (name, rec, fpr, cnt) in enumerate(DATASETS):
        cx = L + slot * (i + 0.5)
        for j, (val, col, lab) in enumerate(((rec, OURS, "recall"), (fpr, WARN, "false pos."))):
            bw = 42
            x = cx - bw - 6 + j * (bw + 12)
            bh = ph * val / 100
            s.append(f'<rect x="{x:.0f}" y="{T+ph-bh:.0f}" width="{bw}" height="{bh:.0f}" '
                     f'fill="{col}" rx="2"/>')
            s.append(f'<text x="{x+bw/2:.0f}" y="{T+ph-bh-7:.0f}" font-size="11.5" '
                     f'font-weight="600" fill="{col}" text-anchor="middle">{val:.1f}%</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+20}" font-size="11.5" fill="{FG}" '
                 f'text-anchor="middle">{name}</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+36}" font-size="10.5" fill="{MUTED}" '
                 f'text-anchor="middle">n={cnt}</text>')
    for j, (col, lab) in enumerate(((OURS, "recall"), (WARN, "false-positive rate"))):
        x = L + j * 150
        s.append(f'<rect x="{x}" y="{H-26}" width="11" height="11" fill="{col}" rx="2"/>')
        s.append(f'<text x="{x+17}" y="{H-16}" font-size="11.5" fill="{MUTED}">{lab}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    depth_chart(out / "recall_by_depth.svg")
    stress_chart(out / "recall_stress.svg")
    dataset_chart(out / "datasets.svg")
    for f in sorted(out.glob("*.svg")):
        print(f"  {f}  ({f.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
