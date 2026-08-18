"""Render a markdown report to a self-contained PDF via headless Chrome.

Chrome rather than a LaTeX toolchain because it is already present on the
machine, and because the charts are SVG: Chrome renders them as vectors, so the
text inside a chart stays selectable and sharp at any zoom, which a raster
conversion would lose.

SVGs referenced with <img src="..."> are INLINED into the document rather than
linked. A headless print has no reliable notion of the working directory for
sub-resources, and inlining also makes the intermediate HTML self-contained, so
it can be opened or shared on its own.

Usage:
    python -m tools.make_pdf --md docs/evaluation.md --out docs/evaluation.pdf
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
]

CSS = """
@page { size: A4; margin: 14mm 15mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 10.2pt; line-height: 1.45; color: #1f2328;
  max-width: 100%; margin: 0; padding: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1 { font-size: 19pt; margin: 0 0 2pt; letter-spacing: -0.2pt; }
h2 { font-size: 12.5pt; margin: 11pt 0 4pt; padding-bottom: 3pt;
     border-bottom: 1px solid #d8dee4; }
h1 + p { color: #57606a; font-size: 9.6pt; margin-top: 0; }
p { margin: 6pt 0; }
hr { display: none; }                     /* the ---- rules waste vertical space in print */
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.2pt; }
th, td { border: 1px solid #d8dee4; padding: 4pt 7pt; text-align: left; }
th { background: #f6f8fa; font-weight: 600; }
td:nth-child(n+3), th:nth-child(n+3) { text-align: right; }
code { background: #f6f8fa; padding: 1pt 3pt; border-radius: 3px;
       font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 8.8pt; }
strong { font-weight: 650; }
figure { margin: 6pt 0; text-align: center; page-break-inside: avoid; }
figure svg { max-width: 86%; height: auto; }
em { color: #57606a; }
ul { margin: 6pt 0; padding-left: 18pt; }
table, figure { page-break-inside: avoid; }
h2 { page-break-after: avoid; }   /* keep a heading with its content */
"""


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


def inline_svgs(html: str, base: Path) -> str:
    """Replace <img src="*.svg"> with the SVG markup itself."""
    def repl(m):
        src = m.group(1)
        p = (base / src).resolve()
        if not p.exists() or p.suffix.lower() != ".svg":
            return m.group(0)
        svg = p.read_text(encoding="utf-8")
        svg = re.sub(r'<\?xml[^>]*\?>', "", svg).strip()
        # let CSS scale it; keep the viewBox so proportions survive
        svg = re.sub(r'\swidth="\d+"', ' width="100%"', svg, count=1)
        svg = re.sub(r'\sheight="\d+"', "", svg, count=1)
        return f"<figure>{svg}</figure>"
    return re.sub(r'<img[^>]*src="([^"]+)"[^>]*/?>', repl, html)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default="docs/evaluation.md")
    ap.add_argument("--out", default="docs/evaluation.pdf")
    ap.add_argument("--keep-html", action="store_true")
    a = ap.parse_args(argv)

    chrome = find_chrome()
    if not chrome:
        print("No Chrome/Chromium found; cannot render PDF.")
        return 1

    md_path = Path(a.md)
    import markdown
    body = markdown.markdown(
        md_path.read_text(encoding="utf-8"),
        extensions=["tables", "attr_list", "sane_lists"])
    body = inline_svgs(body, md_path.parent)

    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{md_path.stem}</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")

    out = Path(a.out).resolve()
    tmp = Path(tempfile.mkdtemp()) / "report.html"
    tmp.write_text(html, encoding="utf-8")

    cmd = [chrome, "--headless", "--disable-gpu", "--no-sandbox",
           "--no-pdf-header-footer", f"--print-to-pdf={out}", tmp.as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if not out.exists():
        print("PDF not produced.")
        print((r.stderr or "")[-600:])
        return 1

    if a.keep_html:
        dest = out.with_suffix(".html")
        dest.write_text(html, encoding="utf-8")
        print(f"  html -> {dest}")
    print(f"  pdf  -> {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
