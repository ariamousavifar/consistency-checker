"""CLI entry point.

Single example, offline (no API key, shipped fixtures):
    python -m src.main --file examples/sample_essay.txt --offline
    python -m src.main --file examples/taxation_essay.txt --offline --bridges examples/taxation_essay.bridges.json

All examples at once (one timestamped folder, one subfolder each):
    python -m src.main --all-examples --offline
    python -m src.main --all-examples --offline --effort 2

Live mode (needs .env with LLM_API_KEY etc.; Groq or NVIDIA NIM):
    python -m src.main --file path/to/your.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .pipeline import run_pipeline
from .schema import StatementType, Verdict
from .tree_builder import build_tree_text


def _inconsistent_sets(report) -> list[tuple]:
    by_id = {p.id: p for p in report.propositions}
    seen, sets = set(), []
    for p in report.propositions:
        if p.verdict == Verdict.CONTRADICTS:
            members = tuple(sorted({p.id, *p.conflict}))
            if members in seen:
                continue
            seen.add(members)
            bridged = any(by_id[m].type == StatementType.BRIDGE for m in members if m in by_id)
            sets.append((members, bridged))
    return sets


def _format_report(report, out_dir: str, show_tree: bool) -> str:
    lines = []
    lines.append(f"\nSource: {report.source_file}   mode: {report.mode}   effort: {report.effort}\n")
    lines.append(f"{'id':<6} {'type':<18} {'gate':<12} {'verdict':<14} {'statement'}")
    lines.append("-" * 100)
    for p in report.propositions:
        verdict = p.verdict.value if p.verdict else "-"
        lines.append(f"{p.id:<6} {p.type.value:<18} {p.status.value:<12} {verdict:<14} {p.decontextualized[:58]}")
    lines.append("")
    for members, bridged in _inconsistent_sets(report):
        tag = " [bridged: relies on a background premise]" if bridged else ""
        lines.append(f"INCONSISTENT SET {{{', '.join(members)}}}: these cannot all be true; "
                     f"at least one must be abandoned (the system does not pick which){tag}")
    if report.screener:
        lines.append(f"\nSurface screener flagged {len(report.screener)} pair(s): "
                     + ", ".join(f"{f['a']}~{f['b']}" for f in report.screener))
    lines.append("\nTiming:")
    for r in report.timing:
        lines.append(f"  {r['stage']:<16}{r['seconds']:>10.4f}s")
    if show_tree:
        lines.append("")
        lines.append(build_tree_text(report))
    lines.append(f"\nFull report: {out_dir}/report.md")
    return "\n".join(lines)


def _print_report(report, out_dir: str, show_tree: bool) -> None:
    print(_format_report(report, out_dir, show_tree))


def _run_all(args) -> int:
    manifest = json.loads(Path("examples/examples.json").read_text())
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eff = f"effort{args.effort}"
    parent = Path(f"out_all_{stamp}_{eff}")
    parent.mkdir(parents=True, exist_ok=True)
    summary = []
    transcript: list[str] = []

    def emit(text: str) -> None:
        print(text)
        transcript.append(text)

    for ex in manifest["examples"]:
        sub = parent / ex["name"]
        header = "\n" + "=" * 100 + f"\nEXAMPLE: {ex['name']}  ::  {ex.get('note', '')}\n" + "=" * 100
        emit(header)
        # Item 15: per-example isolation. One failure (e.g. a model returning
        # prose instead of JSON) must not abort the whole batch.
        try:
            report = run_pipeline(
                file_path=ex["file"],
                offline=args.offline,
                fixtures_dir=args.fixtures,
                out_dir=str(sub),
                solver_timeout_ms=args.solver_timeout_ms,
                effort=args.effort,
                bridges_path=ex.get("bridges"),
                model_overrides=getattr(args, "_overrides", None),
            )
        except Exception as exc:
            msg = f"\n!! ERROR on {ex['name']}: {type(exc).__name__}: {exc}\n   (skipped; batch continues)"
            emit(msg)
            summary.append((ex["name"], "ERR", "ERR", 0.0, str(sub)))
            continue
        emit(_format_report(report, str(sub), show_tree=not args.no_tree))
        sets = _inconsistent_sets(report)
        total_ms = sum(r["seconds"] for r in report.timing) * 1000
        summary.append((ex["name"], len(sets), len(report.screener), total_ms, str(sub)))

    summary_lines = ["\n" + "=" * 100, "SUMMARY (all examples)", "=" * 100]
    summary_lines.append(f"{'example':<24}{'inconsistent_sets':>18}{'screener_flags':>16}{'total_ms':>12}   folder")
    for name, nsets, nflags, ms, folder in summary:
        ms_s = f"{ms:>12.1f}" if isinstance(ms, float) else f"{ms:>12}"
        summary_lines.append(f"{name:<24}{str(nsets):>18}{str(nflags):>16}{ms_s}   {folder}")
    summary_lines.append(f"\nAll outputs under: {parent}/")
    summary_text = "\n".join(summary_lines)
    emit(summary_text)

    # Item 12: one consolidated transcript so results need not be copy-pasted
    # from each report.md by hand.
    consolidated = parent / "all_examples_report.txt"
    consolidated.write_text("\n".join(transcript) + "\n", encoding="utf-8")
    print(f"\nConsolidated transcript: {consolidated}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Internal-inconsistency checker (prototype v0.6)")
    parser.add_argument("--file", help="Path to a .txt document")
    parser.add_argument("--all-examples", action="store_true",
                        help="Run every example in examples/examples.json into one timestamped folder")
    parser.add_argument("--offline", action="store_true", help="Use shipped fixtures instead of an LLM API")
    parser.add_argument("--fixtures", default="examples/fixtures", help="Fixtures directory (offline mode)")
    parser.add_argument("--provider", default=None,
                        help="LLM provider short name (nim, groq). If omitted, prompts or uses .env.")
    parser.add_argument("--model", default=None,
                        help="Model id or short suffix. If omitted, prompts or uses .env.")

    def _next_out() -> str:
        import re
        existing = [p for p in Path(".").iterdir() if re.match(r"^out(\d*)$", p.name) and p.is_dir()]
        nums = [int(re.match(r"^out(\d*)$", p.name).group(1) or 0) for p in existing]
        return "out" if not nums else f"out{max(nums) + 1}"

    parser.add_argument("--out", default=None, help="Output directory (default: next free out/out1/out2...)")
    parser.add_argument("--solver-timeout-ms", type=int, default=8000)
    parser.add_argument("--effort", type=int, default=1, choices=[0, 1, 2, 3],
                        help="0 = surface screener only, 1 = clustered symbolic checks (default), "
                             "2 = global axiom set, 3 = global set + cross-cluster sweep")
    parser.add_argument("--bridges", default=None,
                        help="Optional JSON file of background bridge premises (tagged, never silent)")
    parser.add_argument("--no-tree", action="store_true", help="Skip printing the theory tree to the console")
    args = parser.parse_args(argv)

    # Resolve provider/model once (flags > interactive picker > .env fallback).
    # Skipped entirely in offline mode, which uses no LLM.
    overrides = None
    if not args.offline:
        from .providers import resolve_model_config
        overrides = resolve_model_config(args.provider, args.model)
    args._overrides = overrides

    if args.all_examples:
        return _run_all(args)

    if not args.file:
        parser.error("provide --file PATH or --all-examples")

    out_dir = args.out or _next_out()
    report = run_pipeline(
        file_path=args.file,
        offline=args.offline,
        fixtures_dir=args.fixtures,
        out_dir=out_dir,
        solver_timeout_ms=args.solver_timeout_ms,
        effort=args.effort,
        bridges_path=args.bridges,
        model_overrides=overrides,
    )
    _print_report(report, out_dir, show_tree=not args.no_tree)
    print(f"             (also report.json, store.json, timing.json, theory_tree.txt,")
    print(f"             graph.svg, graph.dot, graph.png if Graphviz is installed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
