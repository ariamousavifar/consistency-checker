"""CLI entry point.

Offline demos (no API key, shipped fixtures):
    python -m src.main --file examples/sample_essay.txt --offline
    python -m src.main --file examples/taxation_essay.txt --offline --bridges examples/taxation_essay.bridges.json

Live mode (needs .env with LLM_API_KEY etc.; Groq or NVIDIA NIM):
    python -m src.main --file path/to/your.txt
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .pipeline import run_pipeline
from .schema import StatementType
from .tree_builder import build_tree_text


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Internal-inconsistency checker (prototype v0.3)")
    parser.add_argument("--file", required=True, help="Path to a .txt document")
    parser.add_argument("--offline", action="store_true", help="Use shipped fixtures instead of an LLM API")
    parser.add_argument("--fixtures", default="examples/fixtures", help="Fixtures directory (offline mode)")
    parser.add_argument("--out", default="out", help="Output directory")
    parser.add_argument("--solver-timeout-ms", type=int, default=8000)
    parser.add_argument("--effort", type=int, default=1, choices=[0, 1, 2],
                        help="0 = surface screener only, 1 = clustered symbolic checks (default), 2 = global axiom set")
    parser.add_argument("--bridges", default=None,
                        help="Optional JSON file of background bridge premises (tagged, never silent)")
    parser.add_argument("--no-tree", action="store_true", help="Skip printing the theory tree to the console")
    args = parser.parse_args(argv)

    report = run_pipeline(
        file_path=args.file,
        offline=args.offline,
        fixtures_dir=args.fixtures,
        out_dir=args.out,
        solver_timeout_ms=args.solver_timeout_ms,
        effort=args.effort,
        bridges_path=args.bridges,
    )

    print(f"\nSource: {report.source_file}   mode: {report.mode}   effort: {report.effort}\n")
    print(f"{'id':<5} {'type':<18} {'gate':<12} {'verdict':<14} {'statement'}")
    print("-" * 100)
    for p in report.propositions:
        verdict = p.verdict.value if p.verdict else "-"
        print(f"{p.id:<5} {p.type.value:<18} {p.status.value:<12} {verdict:<14} {p.decontextualized[:60]}")
    print()

    by_id = {p.id: p for p in report.propositions}
    seen = set()
    for p in report.propositions:
        if p.verdict and p.verdict.value == "contradicts":
            members = tuple(sorted({p.id, *p.conflict}))
            if members in seen:
                continue
            seen.add(members)
            bridged = any(by_id[m].type == StatementType.BRIDGE for m in members if m in by_id)
            tag = " [bridged: relies on a background premise]" if bridged else ""
            print(f"INCONSISTENT SET {{{', '.join(members)}}}: these cannot all be true; "
                  f"at least one must be abandoned (the system does not pick which){tag}")

    if report.screener:
        print(f"\nSurface screener flagged {len(report.screener)} pair(s): "
              + ", ".join(f"{f['a']}~{f['b']}" for f in report.screener))

    print("\nTiming:")
    for r in report.timing:
        print(f"  {r['stage']:<16}{r['seconds']:>10.4f}s")

    if not args.no_tree:
        print()
        print(build_tree_text(report))
    print(f"\nFull report: {args.out}/report.md   (also report.json, store.json, timing.json,")
    print(f"             theory_tree.txt, graph.svg, graph.dot, graph.png if Graphviz is installed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
