"""Pipeline orchestration (the chained architecture spine):
clean -> extract -> translate -> gate -> bridges -> screener -> solve -> report.
Every stage is timed; bridge premises are user-supplied and tagged; the effort
dial controls solver depth (0 = screener only, 1 = clustered, 2 = global set).
"""
from __future__ import annotations

import json
from pathlib import Path

from .cleaning import clean
from .extraction import (
    FixtureExtractor,
    FixtureTranslator,
    LiveExtractor,
    LiveTranslator,
    apply_compound_splitting,
)
from .gate import run_gate
from .llm_client import LLMClient, LLMConfig
from .report import render_markdown
from .schema import GateOutcome, Proposition, RunReport, StatementType
from .screener import screen
from .solver import verify
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
) -> RunReport:
    timer = StageTimer()
    file_path = Path(file_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_name = file_path.stem

    with timer.stage("read_and_clean"):
        raw_text = file_path.read_text(encoding="utf-8")
        doc = clean(raw_text)

    if offline:
        extractor = FixtureExtractor(Path(fixtures_dir), source_name)
        translator = FixtureTranslator(Path(fixtures_dir), source_name)
        mode = "offline (fixtures)"
    else:
        config = LLMConfig()
        if not config.configured:
            raise RuntimeError(
                "Live mode requires LLM_API_KEY (and friends) in the environment or a .env file. "
                "Use --offline to run the shipped examples without an API key."
            )
        client = LLMClient(config)
        extractor = LiveExtractor(client)
        translator = LiveTranslator(client)
        mode = f"live ({config.base_url}, {config.model})"

    with timer.stage("extraction"):
        statements = extractor.extract(doc.raw_text)
        if not offline:
            # Fixtures are authored already-split; only live extraction needs this.
            statements = apply_compound_splitting(statements)

    vocab = Vocabulary()
    with timer.stage("translation"):
        llm_fols = translator.translate(statements, vocab)

    with timer.stage("gate"):
        propositions = []
        for stmt in statements:
            prop = run_gate(stmt, llm_fols.get(stmt.id), vocab)
            prop.span = doc.find_span(stmt.original_text)
            propositions.append(prop)

    with timer.stage("bridges"):
        if bridges_path:
            propositions.extend(_load_bridges(bridges_path, vocab))

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
