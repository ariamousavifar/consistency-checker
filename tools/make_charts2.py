"""Figures for the cross-system comparison (docs/evaluation2.md).

These read their numbers live from tools.compare, which reads results/*/metrics.json,
so a chart cannot disagree with the table beside it. Regenerating after a new run
updates both.

Usage:  python -m tools.make_charts2 --out docs/assets
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tools.compare import MANIFEST, ORDER, load, row

BG, FG, MUTED, GRID = "#ffffff", "#1f2328", "#57606a", "#d8dee4"
COLOUR = {"gpt-oss-120b": "#0969da", "DeepSeek-V4-Flash": "#1a7f37",
          "GLM-4.7": "#9a6700", "rule-only (no LLM)": "#8250df",
          "pairwise NLI": "#cf222e"}
PW = "ProofWriter (n=192)"


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


def data_for(dataset: str) -> dict[str, dict]:
    out = {}
    for name in ORDER:
        src = MANIFEST[dataset].get(name)
        if not src:
            continue
        d = load(src)
        if d:
            out[name] = row(d)
    return out


def matrix_chart(path: Path):
    """Every system on one corpus, across all four detection metrics."""
    rows = data_for(PW)
    metrics = [("recall", "rec"), ("precision", "pre"),
               ("false pos.", "fpr"), ("F1", "f1")]
    W, H, L, R, T, B = 860, 420, 60, 30, 74, 106
    pw, ph = W - L - R, H - T - B
    s = _hdr(W, H, "Every system on the same corpus",
             "ProofWriter, 192 held-out documents, identical scoring")
    _yaxis(s, L, T, pw, ph)
    slot = pw / len(metrics)
    for gi, (label, key) in enumerate(metrics):
        cx = L + slot * (gi + 0.5)
        n = len(rows)
        bw = 30
        for si, (name, r) in enumerate(rows.items()):
            val = r[key] * 100
            x = cx - (n * bw + (n - 1) * 5) / 2 + si * (bw + 5)
            bh = ph * (0 if val != val else val) / 100
            s.append(f'<rect x="{x:.0f}" y="{T+ph-bh:.0f}" width="{bw}" height="{bh:.0f}" '
                     f'fill="{COLOUR[name]}" rx="2"/>')
            s.append(f'<text x="{x+bw/2:.0f}" y="{T+ph-bh-6:.0f}" font-size="9.5" '
                     f'font-weight="600" fill="{COLOUR[name]}" text-anchor="middle">{val:.1f}</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+22}" font-size="12.5" font-weight="600" '
                 f'fill="{FG}" text-anchor="middle">{label}</text>')
    for si, name in enumerate(rows):
        x = L + (si % 3) * 250
        y = H - 46 + (si // 3) * 17
        s.append(f'<rect x="{x}" y="{y}" width="11" height="11" fill="{COLOUR[name]}" rx="2"/>')
        s.append(f'<text x="{x+17}" y="{y+10}" font-size="11.5" fill="{MUTED}">{name}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def f1_chart(path: Path):
    """F1 for every system that ran, on every corpus."""
    W, H, L, R, T, B = 860, 400, 60, 30, 74, 106
    pw, ph = W - L - R, H - T - B
    s = _hdr(W, H, "F1 across all four corpora",
             "a blank slot means that system was not run on that corpus")
    _yaxis(s, L, T, pw, ph)
    slot = pw / len(MANIFEST)
    seen: list[str] = []
    for gi, ds in enumerate(MANIFEST):
        rows = data_for(ds)
        cx = L + slot * (gi + 0.5)
        bw, n = 26, 5
        for si, name in enumerate(ORDER):
            x = cx - (n * bw + (n - 1) * 4) / 2 + si * (bw + 4)
            if name not in rows:
                s.append(f'<line x1="{x:.0f}" y1="{T+ph}" x2="{x+bw:.0f}" y2="{T+ph}" '
                         f'stroke="{GRID}" stroke-width="2"/>')
                continue
            if name not in seen:
                seen.append(name)
            val = rows[name]["f1"] * 100
            bh = ph * (0 if val != val else val) / 100
            s.append(f'<rect x="{x:.0f}" y="{T+ph-bh:.0f}" width="{bw}" height="{bh:.0f}" '
                     f'fill="{COLOUR[name]}" rx="2"/>')
            s.append(f'<text x="{x+bw/2:.0f}" y="{T+ph-bh-6:.0f}" font-size="9" '
                     f'font-weight="600" fill="{COLOUR[name]}" text-anchor="middle">{val:.0f}</text>')
        short = ds.split(" (")[0]
        s.append(f'<text x="{cx:.0f}" y="{T+ph+22}" font-size="12" fill="{FG}" '
                 f'text-anchor="middle">{short}</text>')
        s.append(f'<text x="{cx:.0f}" y="{T+ph+38}" font-size="10.5" fill="{MUTED}" '
                 f'text-anchor="middle">n={ds.split("n=")[1].rstrip(")")}</text>')
    for si, name in enumerate(ORDER):
        x = L + (si % 3) * 250
        y = H - 46 + (si // 3) * 17
        s.append(f'<rect x="{x}" y="{y}" width="11" height="11" fill="{COLOUR[name]}" rx="2"/>')
        s.append(f'<text x="{x+17}" y="{y+10}" font-size="11.5" fill="{MUTED}">{name}</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def precision_recall_chart(path: Path):
    """Precision against recall: the trade-off each system actually makes."""
    W, H, L, R, T, B = 760, 430, 60, 40, 74, 116
    pw, ph = W - L - R, H - T - B
    s = _hdr(W, H, "Precision against recall",
             "ProofWriter; up and to the right is better, and the corner is empty")
    _yaxis(s, L, T, pw, ph)
    for i in range(0, 101, 25):
        x = L + pw * i / 100
        s.append(f'<line x1="{x}" y1="{T}" x2="{x}" y2="{T+ph}" stroke="{GRID}" stroke-width="1"/>')
        s.append(f'<text x="{x}" y="{T+ph+18}" font-size="11" fill="{MUTED}" '
                 f'text-anchor="middle">{i}%</text>')
    s.append(f'<text x="{L+pw/2:.0f}" y="{T+ph+38}" font-size="11.5" fill="{MUTED}" '
             f'text-anchor="middle">recall</text>')
    s.append(f'<text x="18" y="{T+ph/2:.0f}" font-size="11.5" fill="{MUTED}" '
             f'text-anchor="middle" transform="rotate(-90 18 {T+ph/2:.0f})">precision</text>')
    for name, r in data_for(PW).items():
        x = L + pw * r["rec"]
        y = T + ph - ph * r["pre"]
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{COLOUR[name]}"/>')
        anchor = "end" if r["rec"] > 0.7 else "start"
        dx = -12 if anchor == "end" else 12
        s.append(f'<text x="{x+dx:.0f}" y="{y+4:.0f}" font-size="11.5" font-weight="600" '
                 f'fill="{COLOUR[name]}" text-anchor="{anchor}">{name}</text>')
    s.append(f'<text x="{L}" y="{H-16}" font-size="11" fill="{MUTED}">'
             f'the pipeline trades recall for precision by refusing what it cannot represent; '
             f'the pairwise baseline gives up both</text>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/assets")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    matrix_chart(out / "systems-matrix.svg")
    f1_chart(out / "f1-by-dataset.svg")
    precision_recall_chart(out / "precision-recall.svg")
    for n in ("systems-matrix", "f1-by-dataset", "precision-recall"):
        f = out / f"{n}.svg"
        print(f"  {f}  ({f.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
