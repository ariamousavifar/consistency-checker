"""Pipeline orchestration: clean -> extract -> gate -> store -> verify -> report."""
from __future__ import annotations

import json
from pathlib import Path

from .cleaning import clean
from .extraction import FixtureExtractor, FixtureTranslator, LiveExtractor, LiveTranslator
from .gate import run_gate
from .llm_client import LLMClient, LLMConfig
from .report import render_markdown
from .schema import RunReport
from .solver import verify
from .tree_builder import build_dot, build_svg, build_tree_text, render_png
from .vocabulary import Vocabulary


def run_pipeline(
    file_path: str | Path,
    offline: bool,
    fixtures_dir: str | Path = "examples/fixtures",
    out_dir: str | Path = "out",
    solver_timeout_ms: int = 8000,
) -> RunReport:
    file_path = Path(file_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source_name = file_path.stem

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
                "Use --offline to run the shipped example without an API key."
            )
        client = LLMClient(config)
        extractor = LiveExtractor(client)
        translator = LiveTranslator(client)
        mode = f"live ({config.base_url}, {config.model})"

    statements = extractor.extract(doc.raw_text)
    vocab = Vocabulary()
    llm_fols = translator.translate(statements, vocab)

    propositions = []
    for stmt in statements:
        prop = run_gate(stmt, llm_fols.get(stmt.id), vocab)
        prop.span = doc.find_span(stmt.original_text)
        propositions.append(prop)

    clusters = verify(propositions, timeout_ms=solver_timeout_ms)

    report = RunReport(
        source_file=str(file_path),
        mode=mode,
        propositions=propositions,
        clusters=clusters,
        vocabulary_predicates=vocab.predicates,
        vocabulary_constants=vocab.constants,
    )

    (out_dir / "store.json").write_text(
        json.dumps([p.model_dump(mode="json") for p in propositions], indent=2), encoding="utf-8"
    )
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (out_dir / "theory_tree.txt").write_text(build_tree_text(report), encoding="utf-8")
    dot = build_dot(report)
    (out_dir / "graph.dot").write_text(dot, encoding="utf-8")
    (out_dir / "graph.svg").write_text(build_svg(report), encoding="utf-8")
    render_png(dot, out_dir / "graph.png")  # best effort; needs local Graphviz
    return report
