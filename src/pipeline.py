"""Pipeline orchestration (the chained architecture spine):
clean -> extract -> translate -> gate -> bridges -> screener -> solve -> report.
Every stage is timed; bridge premises are user-supplied and tagged; the effort
dial controls solver depth (0 = screener only, 1 = clustered, 2 = global set).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .cleaning import clean
from .extraction import (
    FixtureExtractor,
    FixtureTranslator,
    LiveExtractor,
    LiveTranslator,
    apply_compound_splitting,
)
from .normalize import retype_bare_instances
from .chunked_extraction import extract_chunked
from .gate import run_gate
from .llm_client import LLMClient, LLMConfig
from .semantics import LLMJudge
from .report import render_markdown
from .schema import GateOutcome, Proposition, RunReport, StatementType
from .screener import screen
from .solver import mark_duplicate_fols, verify
from .timing import StageTimer
from .tree_builder import build_dot, build_svg, build_tree_text, render_png
from .vocabulary import Vocabulary


def _load_bridges(bridges_path: str | Path, vocab: Vocabulary) -> list[Proposition]:
    data = json.loads(Path(bridges_path).read_text(encoding="utf-8"))
    out = []
    for b in data:
        gloss = b.get("gloss", b["fol"])
        out.append(
            Proposition(
                id=b["id"],
                type=StatementType.BRIDGE,
                speaker="background",
                original_text=gloss,
                decontextualized=gloss,
                fol=vocab.normalize_fol(b["fol"]),
                status=GateOutcome.ACCEPTED,
                confidence=1.0,
                gate_reason="user-supplied bridge premise (not stated in the text)",
            )
        )
    return out


def run_pipeline(
    file_path: str | Path,
    offline: bool,
    fixtures_dir: str | Path = "examples/fixtures",
    out_dir: str | Path = "out",
    solver_timeout_ms: int = 8000,
    effort: int = 1,
    bridges_path: str | Path | None = None,
    model_overrides: dict | None = None,
    resume: bool = False,
    no_chunk: bool = False,
    use_nli: bool = False,
    allow_conditionals: bool = False,
    guard_deontic: bool = False,
    unify_self_ref: bool = False,
) -> RunReport:
    timer = StageTimer()
    file_path = Path(file_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_name = file_path.stem
    client = None
    # Semantic judge (NLI). Opt-in and live-only: it issues extra LLM calls, so
    # it is never created in offline mode and never unless explicitly enabled,
    # keeping default runs (and the test suite) deterministic and free of cost.
    use_nli = use_nli or os.getenv("LLM_NLI", "").strip().lower() in ("1", "true", "yes", "on")
    judge = None

    with timer.stage("read_and_clean"):
        raw_text = file_path.read_text(encoding="utf-8")
        doc = clean(raw_text)

    if offline:
        extractor = FixtureExtractor(Path(fixtures_dir), source_name)
        translator = FixtureTranslator(Path(fixtures_dir), source_name)
        mode = "offline (fixtures)"
    else:
        config = LLMConfig(overrides=model_overrides)
        if not config.configured:
            raise RuntimeError(
                "Live mode requires an API key (LLM_API_KEY / NIM_API_KEY / GROQ_API_KEY) "
                "in the environment or a .env file. Use --offline to run shipped examples."
            )
        client = LLMClient(config)
        extractor = LiveExtractor(client)
        translator = LiveTranslator(client, allow_conditionals=allow_conditionals)
        if use_nli:
            judge = LLMJudge(client)
        mode = f"live ({config.base_url}, {config.model})"
        if use_nli:
            mode += " [nli]"

    if allow_conditionals:
        mode += " [+conditionals]"
    if guard_deontic:
        mode += " [+deontic-guard]"
    if unify_self_ref:
        mode += " [+self-ref]"

    with timer.stage("extraction"):
        statements, num_chunks = extract_chunked(
            doc, extractor, out_dir,
            offline=offline, resume=resume, no_chunk=no_chunk,
        )

    vocab = Vocabulary(judge=judge)
    with timer.stage("translation"):
        llm_fols = translator.translate(statements, vocab)

    with timer.stage("gate"):
        propositions = []
        for stmt in statements:
            prop = run_gate(stmt, llm_fols.get(stmt.id), vocab, judge=judge, guard_deontic=guard_deontic)
            prop.span = doc.find_span(stmt.original_text)
            propositions.append(prop)

    with timer.stage("bridges"):
        if bridges_path:
            propositions.extend(_load_bridges(bridges_path, vocab))

    # Deterministic, document-scoped predicate unification (no LLM): now that
    # every predicate the document uses is registered, merge a lone modified form
    # onto its bare head ('FellowOfAcademy' -> 'Fellow') so a chain split across
    # bare/modified phrasings reconnects inside Z3. Reproducible and safe (a head
    # with two competing modifiers is left untouched). Rewrites every emitted FOL.
    with timer.stage("unify_predicates"):
        aliases = vocab.finalize_modifier_aliases()
        # Self-reference constant unification (opt-in): merge author/speaker/I/...
        # so a bridge written against 'author' connects to text that emitted
        # 'speaker'. Bridges are already in `propositions` here, so their FOL is
        # rewritten too. Single-author scope (see finalize_self_reference_aliases).
        const_aliases = vocab.finalize_self_reference_aliases() if unify_self_ref else {}
        if aliases or const_aliases:
            for prop in propositions:
                if prop.fol:
                    if aliases:
                        prop.fol = vocab.apply_pred_aliases(prop.fol, aliases)
                    if const_aliases:
                        prop.fol = vocab.apply_const_aliases(prop.fol, const_aliases)

    # Deduplicate AFTER vocabulary unification (so predicate-aligned statements
    # that became identical are caught) and BEFORE the solver (so duplicates
    # can't seed spurious 'X proved from X' derivation edges or inflate counts).
    with timer.stage("dedup"):
        mark_duplicate_fols(propositions)

    with timer.stage("screener"):
        flags = screen([(s.id, s.decontextualized) for s in statements])

    with timer.stage("solver"):
        clusters = verify(propositions, timeout_ms=solver_timeout_ms, effort=effort) if effort >= 1 else []

    report = RunReport(
        source_file=str(file_path),
        mode=mode,
        propositions=propositions,
        clusters=clusters,
        vocabulary_predicates=vocab.predicates,
        vocabulary_constants=vocab.constants,
        effort=effort,
        timing=list(timer.records),
        screener=flags,
        usage=client.usage() if client is not None else {},
        chunked=(num_chunks > 1),
        num_chunks=num_chunks,
    )

    with timer.stage("write_outputs"):
        (out_dir / "store.json").write_text(
            json.dumps([p.model_dump(mode="json") for p in propositions], indent=2), encoding="utf-8"
        )
        (out_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
        (out_dir / "theory_tree.txt").write_text(build_tree_text(report), encoding="utf-8")
        dot = build_dot(report)
        (out_dir / "graph.dot").write_text(dot, encoding="utf-8")
        (out_dir / "graph.svg").write_text(build_svg(report), encoding="utf-8")
        render_png(dot, out_dir / "graph.png")
    (out_dir / "timing.json").write_text(json.dumps(timer.records, indent=2), encoding="utf-8")
    return report
