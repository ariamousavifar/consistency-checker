"""Render the evaluation charts as standalone SVG.

Hand-written SVG rather than a plotting library: it adds no dependency, the
output is small and diffable, and GitHub renders it inline. Colours are chosen to
stay legible on both light and dark backgrounds, and every chart paints its own
background so it never inherits the host page's.

Usage:  python -m tools.make_charts --out docs/assets
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BG, FG, MUTED, GRID = "#ffffff", "#1f2328", "#57606a", "#d8dee4"
SERIES = {"gpt-oss-120b": "#0969da", "DeepSeek-V4-Flash": "#1a7f37",
          "GLM-4.7": "#9a6700", "rule-only (no LLM)": "#cf222e"}


def _hdr(w, h, title, sub=""):
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
         f'viewBox="0 0 {w} {h}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">',
         f'<rect width="{w}" height="{h}" fill="{BG}"/>',
         f'<text x="24" y="30" font-size="16" font-weight="600" fill="{FG}">{title}</text>']
    if sub:
        s.append(f'<text x="24" y="50" font-size="12" fill="{MUTED}">{sub}</text>')
    return s


def depth_chart(data: dict, path: Path):
    """Recall as a function of reasoning depth -- the central result."""
    W, H = 720, 400
    L, R, T, B = 60, 250, 74, 54
    pw, ph = W - L - R, H - T - B
    s = _hdr(W, H, "Recall vs. reasoning depth",
             "ProofWriter (192 held-out documents, externally authored)")
    for i in range(0, 101, 25):                      # y grid
        y = T + ph - ph * i / 100
        s.append(f'<line x1="{L}" y1="{y}" x2="{L+pw}" y2="{y}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{L-10}" y="{y+4}" font-size="11" fill="{MUTED}" text-anchor="end">{i}%</text>')
    for d in range(6):                               # x labels
        x = L + pw * d / 5
        s.append(f'<text x="{x}" y="{T+ph+20}" font-size="11" fill="{MUTED}" text-anchor="middle">{d}</text>')
    s.append(f'<text x="{L+pw/2}" y="{T+ph+42}" font-size="12" fill="{FG}" '
             f'text-anchor="middle">inference steps between the conflict and its source</text>')

    for li, (name, vals) in enumerate(data.items()):
        col = SERIES[name]
        pts = " ".join(f"{L+pw*d/5},{T+ph-ph*vals[d]/100}" for d in range(6))
        dash = ' stroke-dasharray="6 4"' if "rule-only" in name else ""
        s.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.5"{dash}/>')
        for d in range(6):
            s.append(f'<circle cx="{L+pw*d/5}" cy="{T+ph-ph*vals[d]/100}" r="3.5" fill="{col}"/>')
        y = T + 6 + li * 22
        s.append(f'<line x1="{L+pw+22}" y1="{y}" x2="{L+pw+44}" y2="{y}" stroke="{col}" stroke-width="2.5"{dash}/>')
        s.append(f'<text x="{L+pw+50}" y="{y+4}" font-size="12" fill="{FG}">{name}</text>')

    s.append(f'<text x="{L+pw+22}" y="{T+6+len(data)*22+22}" font-size="11" fill="{MUTED}">'
             f'Sentence-pair methods</text>')
    s.append(f'<text x="{L+pw+22}" y="{T+6+len(data)*22+38}" font-size="11" fill="{MUTED}">'
             f'reach 0% at depth &#8805; 2</text>')
    s.append(f'<text x="{L+pw+22}" y="{T+6+len(data)*22+54}" font-size="11" fill="{MUTED}">'
             f'by construction.</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def model_chart(rows: list, path: Path):
    """Recall / precision / false-positive per model, grouped."""
    W, H = 720, 360
    L, T, B = 150, 78, 60
    pw, ph = W - L - 40, H - T - B
    bar, gap = 16, 6
    s = _hdr(W, H, "Model comparison", "ProofWriter, 192 documents, identical pipeline and seed")
    metrics = [("recall", "#0969da"), ("precision", "#1a7f37"), ("false-positive", "#cf222e")]
    for i in range(0, 101, 25):
        x = L + pw * i / 100
        s.append(f'<line x1="{x}" y1="{T}" x2="{x}" y2="{T+ph}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{x}" y="{T+ph+18}" font-size="11" fill="{MUTED}" text-anchor="middle">{i}%</text>')
    rh = ph / len(rows)
    for ri, (name, rec, pre, fpr) in enumerate(rows):
        y0 = T + ri * rh + 8
        s.append(f'<text x="{L-12}" y="{y0+26}" font-size="12" fill="{FG}" text-anchor="end">{name}</text>')
        for mi, (val, (label, col)) in enumerate(zip((rec, pre, fpr), metrics)):
            y = y0 + mi * (bar + gap)
            w = max(1.5, pw * val / 100)
            s.append(f'<rect x="{L}" y="{y}" width="{w}" height="{bar}" fill="{col}" rx="2"/>')
            s.append(f'<text x="{L+w+6}" y="{y+12}" font-size="11" fill="{FG}">{val:.1f}%</text>')
    for mi, (label, col) in enumerate(metrics):
        x = L + mi * 150
        s.append(f'<rect x="{x}" y="{H-30}" width="11" height="11" fill="{col}" rx="2"/>')
        s.append(f'<text x="{x+16}" y="{H-20}" font-size="11" fill="{FG}">{label}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def dataset_chart(rows: list, path: Path):
    """Headline metrics per held-out dataset."""
    W, H = 720, 300
    L, T, B = 130, 78, 56
    pw, ph = W - L - 60, H - T - B
    s = _hdr(W, H, "Results by held-out dataset",
             "no dataset was used during development; gpt-oss-120b, seed 7")
    for i in range(0, 101, 25):
        x = L + pw * i / 100
        s.append(f'<line x1="{x}" y1="{T}" x2="{x}" y2="{T+ph}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{x}" y="{T+ph+18}" font-size="11" fill="{MUTED}" text-anchor="middle">{i}%</text>')
    rh = ph / len(rows)
    for ri, (name, n, rec, pre, fpr) in enumerate(rows):
        y0 = T + ri * rh + 6
        s.append(f'<text x="{L-12}" y="{y0+16}" font-size="12" fill="{FG}" text-anchor="end">{name}</text>')
        s.append(f'<text x="{L-12}" y="{y0+31}" font-size="10" fill="{MUTED}" text-anchor="end">n={n}</text>')
        for mi, (val, col) in enumerate(((rec, "#0969da"), (pre, "#1a7f37"), (fpr, "#cf222e"))):
            y = y0 + mi * 17
            w = max(1.5, pw * val / 100)
            s.append(f'<rect x="{L}" y="{y}" width="{w}" height="13" fill="{col}" rx="2"/>')
            s.append(f'<text x="{L+w+6}" y="{y+11}" font-size="10" fill="{FG}">{val:.1f}%</text>')
    for mi, (label, col) in enumerate((("recall", "#0969da"), ("precision", "#1a7f37"),
                                       ("false-positive", "#cf222e"))):
        x = L + mi * 150
        s.append(f'<rect x="{x}" y="{H-28}" width="11" height="11" fill="{col}" rx="2"/>')
        s.append(f'<text x="{x+16}" y="{H-18}" font-size="11" fill="{FG}">{label}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    depth_chart({
        "gpt-oss-120b":       [93.8, 93.8, 81.2, 56.2, 75.0, 81.2],
        "DeepSeek-V4-Flash":  [81.2, 87.5, 75.0, 62.5, 56.2, 62.5],
        "GLM-4.7":            [93.8, 93.8, 56.2, 43.8, 31.2, 62.5],
        "rule-only (no LLM)": [43.8, 31.2,  0.0,  0.0,  0.0,  0.0],
    }, out / "recall-by-depth.svg")

    model_chart([
        ("gpt-oss-120b",      80.2, 97.5, 2.1),
        ("DeepSeek-V4-Flash", 70.8, 98.6, 1.0),
        ("GLM-4.7",           63.5, 96.8, 2.1),
        ("rule-only (no LLM)", 12.5, 92.3, 1.0),
    ], out / "model-comparison.svg")

    dataset_chart([
        ("ProofWriter",  192, 80.2, 97.5, 2.1),
        ("FOLIO",        141, 50.0, 92.3, 4.3),
        ("Synthetic",    120, 100.0, 100.0, 0.0),
    ], out / "dataset-results.svg")

    for f in sorted(out.glob("*.svg")):
        print(f"  {f}  ({f.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
