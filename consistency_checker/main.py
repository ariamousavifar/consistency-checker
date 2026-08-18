"""CLI entry point.

Single example, offline (no API key, shipped fixtures):
    python -m consistency_checker.main --file examples/sample_essay.txt --offline
    python -m consistency_checker.main --file examples/taxation_essay.txt --offline --bridges examples/taxation_essay.bridges.json

All examples at once (one timestamped folder, one subfolder each):
    python -m consistency_checker.main --all-examples --offline
    python -m consistency_checker.main --all-examples --offline --effort 2

Live mode (needs .env with LLM_API_KEY etc.; Groq or NVIDIA NIM):
    python -m consistency_checker.main --file path/to/your.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# All run outputs land under this folder so the repo root stays clean.
# A bare relative path (not absolute) so it sits next to the code the run came from.
RESULTS_DIR = Path("results")

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
    for c in report.clusters:
        if c.refutation:
            # N21: only point at "the tree below" when the tree is actually printed.
            tail = " (see the refutation tree below)" if show_tree else ""
            lines.append(f"DERIVATION: the contradiction is reached by reasoning -- "
                         f"{c.refutation['left_label']}  vs  {c.refutation['right_label']}{tail}")
    for p in report.propositions:
        if p.verdict == Verdict.REFUTED:
            conflict = ", ".join(p.conflict) if p.conflict else "the theory"
            lines.append(f"REDUCTIO {p.id}: supposition contradicts {{{conflict}}}; "
                         f"its negation is proven (the author refutes it on purpose)")
    if report.screener:
        lines.append(f"\nSurface screener flagged {len(report.screener)} pair(s): "
                     + ", ".join(f"{f['a']}~{f['b']}" for f in report.screener))
    shapes: dict[str, int] = {}
    for p in report.propositions:
        if p.quarantine_shape:
            shapes[p.quarantine_shape] = shapes.get(p.quarantine_shape, 0) + 1
    if shapes:
        ordered = sorted(shapes.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(shapes.values())
        lines.append(f"\nQuarantine shapes [{total} outside-fragment] (heuristic; informs EPR-vs-DL): "
                     + ", ".join(f"{k} {v}" for k, v in ordered))
    lines.append("\nTiming:")
    for r in report.timing:
        lines.append(f"  {r['stage']:<16}{r['seconds']:>10.4f}s")
    total_s = sum(r["seconds"] for r in report.timing)
    # Usage / cost line (v0.7.5): calls + tokens, plus chunk mode so chunking
    # overhead can be compared against a --no-chunk run of the same document.
    u = report.usage or {}
    mode = f"chunked x{report.num_chunks}" if report.chunked else "single-pass"
    if u:
        lines.append(
            f"\nUsage [{mode}]: {u.get('calls', 0)} calls, "
            f"{u.get('total_tokens', 0)} tokens "
            f"(prompt {u.get('prompt_tokens', 0)} / completion {u.get('completion_tokens', 0)}), "
            f"{total_s:.1f}s total"
        )
    else:
        lines.append(f"\nUsage [{mode}]: {total_s:.1f}s total (offline; no token usage)")
    if show_tree:
        lines.append("")
        lines.append(build_tree_text(report))
    lines.append(f"\nFull report: {out_dir}/report.md")
    return "\n".join(lines)


def _print_report(report, out_dir: str, show_tree: bool) -> None:
    print(_format_report(report, out_dir, show_tree))


def _run_all(args) -> int:
    manifest = json.loads(Path("examples/examples.json").read_text())
    examples = manifest["examples"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eff = f"effort{args.effort}"

    # Tier filter: --tier N runs only that tier; --all-examples (no tier) runs all.
    tier_tag = ""
    if getattr(args, "tier", None):
        examples = [e for e in examples if e.get("tier") == args.tier]
        tier_tag = f"_tier{args.tier}"
        if not examples:
            print(f"No examples with tier {args.tier} in examples.json.")
            return 1

    # Resume into an existing folder if given; else make a fresh timestamped one.
    if getattr(args, "resume", None):
        parent = Path(args.resume)
        if not parent.exists():
            print(f"Resume folder {parent} does not exist.")
            return 1
        print(f"Resuming into {parent}")
    else:
        parent = RESULTS_DIR / f"out_all_{stamp}{tier_tag}_{eff}"
    parent.mkdir(parents=True, exist_ok=True)
    summary = []
    transcript: list[str] = []

    def emit(text: str) -> None:
        print(text)
        transcript.append(text)

    for ex in examples:
        sub = parent / ex["name"]
        header = "\n" + "=" * 100 + f"\nEXAMPLE: {ex['name']}  ::  {ex.get('note', '')}\n" + "=" * 100

        # Example-level resume: if a report.json already exists from a previous
        # run, the example completed successfully -- skip it entirely rather than
        # re-running from scratch. Chunk-level caching handles partial examples
        # (where extraction started but the example never finished).
        if getattr(args, "resume", None) and (sub / "report.json").exists():
            emit(header)
            emit(f"  [resume] skipping {ex['name']}, already completed (report.json exists)")
            summary.append((ex["name"], "done", "done", 0.0, "-", str(sub)))
            continue

        emit(header)
        # Item 15: per-example isolation. One failure (e.g. a model returning
        # prose instead of JSON) must not abort the whole batch.
        # N20: each example may declare its own fragment flags in examples.json
        # ("flags": {"allow_relations": true, ...}). The batch otherwise blanket-
        # applies one CLI flag set to every example, which mixes regimes -- e.g.
        # forcing --allow-relations onto a normative text (Rothbard) manufactures a
        # false positive. A per-example flag, when present, wins over the CLI flag.
        ex_flags = ex.get("flags", {}) or {}

        def _flag(name):
            return ex_flags.get(name, getattr(args, name, False))

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
                resume=bool(getattr(args, "resume", None)),
                no_chunk=getattr(args, "no_chunk", False),
                use_nli=getattr(args, "nli", False),
                allow_conditionals=_flag("allow_conditionals"),
                guard_deontic=_flag("guard_deontic"),
                unify_self_ref=_flag("unify_self_ref"),
                allow_relations=_flag("allow_relations"),
                prune_derivation=getattr(args, "prune_derivation", False),
            )
        except Exception as exc:
            msg = f"\n!! ERROR on {ex['name']}: {type(exc).__name__}: {exc}\n   (skipped; batch continues)"
            emit(msg)
            summary.append((ex["name"], "ERR", "ERR", 0.0, "ERR", str(sub)))
            continue
        emit(_format_report(report, str(sub), show_tree=not args.no_tree))
        sets = _inconsistent_sets(report)
        total_ms = sum(r["seconds"] for r in report.timing) * 1000
        toks = (report.usage or {}).get("total_tokens", 0)
        summary.append((ex["name"], len(sets), len(report.screener), total_ms, toks, str(sub)))

    summary_lines = ["\n" + "=" * 100, "SUMMARY (all examples)", "=" * 100]
    summary_lines.append(f"{'example':<24}{'inconsistent_sets':>18}{'screener_flags':>16}{'total_ms':>12}{'tokens':>10}   folder")
    for row in summary:
        name, nsets, nflags, ms, toks, folder = row
        ms_s = f"{ms:>12.1f}" if isinstance(ms, float) else f"{ms:>12}"
        summary_lines.append(f"{name:<24}{str(nsets):>18}{str(nflags):>16}{ms_s}{str(toks):>10}   {folder}")
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
    _ENV_EPILOG = """\
environment variables (no CLI flag; set as a PREFIX before the command,
e.g.  LLM_EXTRACTION_EFFORT=low LLM_TRANSLATION_EFFORT=medium python -m consistency_checker.main ...):

  per-stage reasoning effort (override the model default):
    LLM_EXTRACTION_EFFORT          effort for extraction (e.g. low -- keeps dense chunks under the token cap)
    LLM_TRANSLATION_EFFORT         effort for translation (e.g. medium)
    LLM_REASONING_EFFORT           global fallback used when the per-stage vars are unset
  translation retry:
    LLM_TRANSLATION_RETRY          re-ask null/unparseable statements one at a time (1=on default, 0=off)
    LLM_TRANSLATION_RETRY_EFFORT   effort for that retry pass (default: medium)
  connection / pacing:
    LLM_MIN_INTERVAL               minimum seconds between LLM calls (rate-limit pacing; eases 429s)
    LLM_MAX_TOKENS                 max completion tokens per call
    LLM_MAX_RETRIES                client retry count on transient errors
    LLM_API_KEY / LLM_BASE_URL     generic key / endpoint override
    provider keys: CEREBRAS_API_KEY, GROQ_API_KEY, NIM_API_KEY (or NVIDIA_API_KEY), GEMINI_API_KEY (or GOOGLE_API_KEY)
  mirror a CLI flag (the flag wins if both are given):
    LLM_SEED (--seed), LLM_TEMPERATURE (--temperature), LLM_MODEL (--model), LLM_NLI (--nli)
"""
    parser = argparse.ArgumentParser(
        description="Internal-inconsistency checker (FOL + Z3 consistency / theory-tree analysis).",
        epilog=_ENV_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--file", help="Path to a .txt document")
    parser.add_argument("--all-examples", action="store_true",
                        help="Run every example in examples/examples.json into one timestamped folder")
    parser.add_argument("--tier", type=int, default=None,
                        help="Run only examples with this tier (1, 2, 3...). Filters examples.json. "
                             "Without it, --all-examples runs everything.")
    parser.add_argument("--resume", default=None,
                        help="Resume an interrupted batch: pass the results/out_all_... folder to "
                             "reuse already-extracted chunks and finished examples.")
    parser.add_argument("--offline", action="store_true", help="Use shipped fixtures instead of an LLM API")
    parser.add_argument("--fixtures", default="examples/fixtures", help="Fixtures directory (offline mode)")
    parser.add_argument("--provider", default=None,
                        help="LLM provider short name (cerebras, groq, nim). If omitted, prompts or uses .env.")
    parser.add_argument("--model", default=None,
                        help="Model id or short suffix. If omitted, prompts or uses .env.")
    parser.add_argument("--seed", type=int, default=7,
                        help="Determinism seed sent to the LLM (default 7). At the default "
                             "temperature 0 the seed is an on/off switch, not a sampling knob: "
                             "PROVIDING any seed pins reproducible output (the value is irrelevant "
                             "-- seed 7 == seed 21), while a negative value OMITS the seed and "
                             "allows minor backend non-determinism. To test sampling robustness, "
                             "vary --temperature (>0), not the seed.")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Sampling temperature override (default: env LLM_TEMPERATURE or 0).")

    def _next_out() -> str:
        import re
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        existing = [p for p in RESULTS_DIR.iterdir() if re.match(r"^out(\d*)$", p.name) and p.is_dir()]
        nums = [int(re.match(r"^out(\d*)$", p.name).group(1) or 0) for p in existing]
        return str(RESULTS_DIR / ("out" if not nums else f"out{max(nums) + 1}"))

    parser.add_argument("--out", default=None,
                        help="Output directory (default: next free results/out, results/out1, ...)")
    parser.add_argument("--solver-timeout-ms", type=int, default=8000)
    parser.add_argument("--effort", type=int, default=1, choices=[0, 1, 2, 3],
                        help="0 = surface screener only, 1 = clustered symbolic checks (default), "
                             "2 = global axiom set, 3 = global set + cross-cluster sweep")
    parser.add_argument("--bridges", default=None,
                        help="Optional JSON file of background bridge premises (tagged, never silent)")
    parser.add_argument("--no-tree", action="store_true", help="Skip printing the theory tree to the console")
    parser.add_argument("--nli", action="store_true",
                        help="Enable the NLI semantic judge (live only; extra LLM calls). "
                             "DISCOURAGED: in testing it added large gate-stage latency for no "
                             "change in verdict -- the deterministic modifier-divergence path "
                             "already handles its target cases. Env: LLM_NLI=1")
    parser.add_argument("--no-chunk", action="store_true",
                        help="Force single-pass extraction even on long documents (control "
                             "condition for measuring chunking overhead; may fail/truncate on "
                             "very long inputs).")
    parser.add_argument("--allow-conditionals", action="store_true",
                        help="Relax translation to keep conditional/disjunctive and deontic "
                             "structure (reified as named predicates) instead of nulling it. "
                             "Lets if/then/either-or arguments enter the solver. Live only.")
    parser.add_argument("--guard-deontic", action="store_true",
                        help="Quarantine prescriptive (ought/should/must/entitled) statements so "
                             "norms stay out of the descriptive (is) axiom set. Pairs with "
                             "--allow-conditionals to control is/ought false positives.")
    parser.add_argument("--unify-self-ref", action="store_true",
                        help="Merge first-person self-reference constants (author/speaker/I/...) "
                             "to one entity so a bridge written against 'author' connects to text "
                             "that emitted 'speaker'. Single-author docs only (not multi-speaker).")
    parser.add_argument("--allow-relations", action="store_true",
                        help="Admit binary relations (G owns R, rule-over, located-in) -- the EPR "
                             "fragment Z3 decides completely. Includes conditional/deontic handling. "
                             "A relational forall/exists role-restriction is set aside for description "
                             "logic. Off by default.")
    parser.add_argument("--prune-derivation", action="store_true",
                        help="When a set is inconsistent, prune the refutation tree/graph to ONLY the "
                             "nodes on the two chains that collide. Off by default: the full "
                             "forward-chaining closure (every derived fact) is shown.")
    args = parser.parse_args(argv)

    # Resolve provider/model once (flags > interactive picker > .env fallback).
    # Skipped entirely in offline mode, which uses no LLM.
    overrides = None
    if not args.offline:
        from .providers import resolve_model_config
        overrides = resolve_model_config(args.provider, args.model)
        # Inject determinism controls. A negative --seed means "omit the seed".
        # These apply even when resolve_model_config returned None (.env fallback).
        if (args.seed is not None and args.seed >= 0) or args.temperature is not None:
            overrides = dict(overrides or {})
            if args.seed is not None and args.seed >= 0:
                overrides["seed"] = args.seed
            if args.temperature is not None:
                overrides["temperature"] = args.temperature
    args._overrides = overrides

    if args.all_examples or args.tier or args.resume:
        return _run_all(args)

    if not args.file:
        parser.error("provide --file PATH, --all-examples, or --tier N")

    out_dir = args.out or _next_out()
    # N23: single-file runs get the same failure isolation as the batch path -- an
    # LLM error (rate-limit/quota, empty or non-JSON response, model incompat)
    # should print a clean message, not a raw traceback with an empty output folder.
    try:
        report = run_pipeline(
            file_path=args.file,
            offline=args.offline,
            fixtures_dir=args.fixtures,
            out_dir=out_dir,
            solver_timeout_ms=args.solver_timeout_ms,
            effort=args.effort,
            bridges_path=args.bridges,
            model_overrides=overrides,
            resume=bool(args.resume),
            no_chunk=args.no_chunk,
            use_nli=getattr(args, "nli", False),
            allow_conditionals=args.allow_conditionals,
            guard_deontic=args.guard_deontic,
            unify_self_ref=args.unify_self_ref,
            allow_relations=args.allow_relations,
            prune_derivation=args.prune_derivation,
        )
    except KeyboardInterrupt:
        print("\n!! interrupted; partial outputs (if any) are in", out_dir)
        return 130
    except Exception as exc:
        print(f"\n!! ERROR: {type(exc).__name__}: {exc}")
        print(f"   (no report written to {out_dir}; the model/provider likely failed "
              f"-- try another provider, lower load with LLM_MIN_INTERVAL, or wait out a rate limit)")
        return 1
    _print_report(report, out_dir, show_tree=not args.no_tree)
    print(f"             (also report.json, store.json, timing.json, theory_tree.txt,")
    print(f"             graph.svg, graph.dot, graph.png if Graphviz is installed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
