"""v0.3 feature tests: negation mapping, bridge premises, effort dial,
surface screener, timing, and neutral report language. All offline."""
from __future__ import annotations

import json

from src.fidelity import fidelity_check
from src.fol_parser import parse_fol
from src.gate import run_gate
from src.pipeline import run_pipeline
from src.rule_translator import rule_translate
from src.schema import (
    ExtractedStatement,
    GateOutcome,
    Proposition,
    StatementType,
    Verdict,
)
from src.screener import screen
from src.solver import verify
from src.verbalizer import verbalize
from src.vocabulary import Vocabulary


def _stmt(id, type, text, decon):
    return ExtractedStatement(id=id, type=type, original_text=text, decontextualized=decon)


def _accepted(id, type, fol):
    return Proposition(
        id=id, type=type, original_text=id, decontextualized=id, fol=fol,
        status=GateOutcome.ACCEPTED, confidence=0.9,
    )


# ---------- Negation mapping (vocabulary alignment) ----------

def test_negation_mapping_rewrites_to_not():
    v = Vocabulary()
    v.canonical_pred("Mortal")
    assert v.normalize_fol("Immortal(socrates)") == "not Mortal(socrates)"
    v.canonical_pred("Human")
    assert v.normalize_fol("forall x. (NonHuman(x) -> Strange(x))") == (
        "forall x. (not Human(x) -> Strange(x))"
    )


def test_negation_mapping_requires_known_base():
    v = Vocabulary()
    # 'Internal' must NOT be split into in + ternal when no Ternal predicate exists
    assert v.normalize_fol("Internal(x0)") == "Internal(x0)"
    # and once mapped, the mapping is recorded
    v.canonical_pred("Just")
    v.normalize_fol("Unjust(act)")
    assert v.negation_mappings.get("unjust") == "Just"


def test_rule_translator_handles_negated_adjective():
    v = Vocabulary()
    v.canonical_pred("Mortal")
    assert rule_translate("Socrates is immortal.", v) == "not Mortal(socrates)"
    # double negation collapses
    assert rule_translate("Socrates is not immortal.", v) == "Mortal(socrates)"


def test_fidelity_accepts_negation_mapped_translation():
    v = Vocabulary()
    v.canonical_pred("Mortal")
    res = fidelity_check("not Mortal(socrates)", "Socrates is immortal.")
    assert res.passed


def test_negation_recovers_contradiction_end_to_end():
    """Reproduces the live-run failure: 'Socrates is immortal' must contradict
    the mortality chain instead of becoming an opaque Immortal predicate."""
    v = Vocabulary()
    axioms = [
        ("a1", "Every philosopher is human.", "forall x. (Philosopher(x) -> Human(x))"),
        ("a2", "Every human is mortal.", "forall x. (Human(x) -> Mortal(x))"),
        ("a3", "Socrates is a philosopher.", "Philosopher(socrates)"),
    ]
    props = []
    for sid, text, llm in axioms:
        props.append(run_gate(_stmt(sid, StatementType.AXIOM, text, text), llm, v))
    claim = run_gate(
        _stmt("c1", StatementType.DERIVED_CLAIM, "Socrates is immortal.", "Socrates is immortal."),
        "Immortal(socrates)",  # the bad LLM habit observed on Groq
        v,
    )
    props.append(claim)
    assert all(p.status == GateOutcome.ACCEPTED for p in props)
    assert claim.fol == "not Mortal(socrates)"
    verify(props)
    assert claim.verdict == Verdict.CONTRADICTS
    assert set(claim.conflict) == {"a1", "a2", "a3"}


# ---------- Bridge premises ----------

def test_bridge_axioms_close_the_taxation_gap(tmp_path):
    no_bridge = run_pipeline(
        file_path="examples/taxation_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path / "plain",
    )
    t3 = {p.id: p for p in no_bridge.propositions}["t3"]
    assert t3.verdict == Verdict.NOT_ENTAILED  # loyal to the text: no contradiction

    bridged = run_pipeline(
        file_path="examples/taxation_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path / "bridged",
        bridges_path="examples/taxation_essay.bridges.json",
    )
    by_id = {p.id: p for p in bridged.propositions}
    assert by_id["t3"].verdict == Verdict.CONTRADICTS
    assert "b1" in by_id["t3"].conflict and "t1" in by_id["t3"].conflict
    assert by_id["b1"].type == StatementType.BRIDGE
    md = (tmp_path / "bridged" / "report.md").read_text()
    assert "(bridged)" in md
    assert "only detectable if you also accept b1" in md


def test_bridge_nodes_render_in_graphs(tmp_path):
    from src.tree_builder import build_dot, build_tree_text

    report = run_pipeline(
        file_path="examples/taxation_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path,
        bridges_path="examples/taxation_essay.bridges.json",
    )
    dot = build_dot(report)
    assert "b1" in dot and "#EEEDFE" in dot
    tree = build_tree_text(report)
    assert "bridge premises (1)" in tree and "BR" in tree


# ---------- Effort dial ----------

def test_effort_one_keeps_clusters_separate():
    props = [
        _accepted("a1", StatementType.AXIOM, "forall x. (Cat(x) -> Animal(x))"),
        _accepted("a2", StatementType.AXIOM, "Cat(felix)"),
        _accepted("b1", StatementType.AXIOM, "forall x. (Star(x) -> Bright(x))"),
        _accepted("b2", StatementType.AXIOM, "Star(sun)"),
    ]
    assert len(verify(props, effort=1)) == 2


def test_effort_two_merges_into_global_set():
    props = [
        _accepted("a1", StatementType.AXIOM, "forall x. (Cat(x) -> Animal(x))"),
        _accepted("b1", StatementType.AXIOM, "forall x. (Star(x) -> Bright(x))"),
    ]
    reports = verify(props, effort=2)
    assert len(reports) == 1
    assert "global axiom set" in reports[0].note


def test_effort_zero_skips_solver(tmp_path):
    report = run_pipeline(
        file_path="examples/sample_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path, effort=0,
    )
    assert report.clusters == []
    assert all(p.verdict is None for p in report.propositions)
    assert report.effort == 0
    # the screener still runs at effort 0
    assert len(report.screener) >= 1


# ---------- Surface screener ----------

def test_screener_flags_polarity_pair():
    flags = screen([
        ("a", "Socrates is mortal."),
        ("b", "Socrates is not mortal."),
        ("c", "Plato wrote many dialogues."),
    ])
    assert len(flags) == 1
    assert {flags[0]["a"], flags[0]["b"]} == {"a", "b"}


def test_screener_flags_prefix_antonyms():
    flags = screen([
        ("a", "The policy is fair."),
        ("b", "The policy is unfair."),
    ])
    assert len(flags) == 1
    assert "antonym" in flags[0]["signal"]


def test_screener_misses_multi_hop_by_design():
    flags = screen([
        ("a", "Every tax is a theft."),
        ("b", "Some taxes are morally justified."),
    ])
    assert flags == []  # this is exactly what the symbolic path exists for


def test_screener_integrated_in_essay_run(tmp_path):
    report = run_pipeline(
        file_path="examples/sample_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path,
    )
    assert any(f["a"] == "s7" and f["b"] == "s8" for f in report.screener)


# ---------- Timing ----------

def test_timing_recorded_and_written(tmp_path):
    report = run_pipeline(
        file_path="examples/sample_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path,
    )
    stages = {r["stage"] for r in report.timing}
    assert {"read_and_clean", "extraction", "translation", "gate", "screener", "solver"} <= stages
    assert all(r["seconds"] >= 0 for r in report.timing)
    written = json.loads((tmp_path / "timing.json").read_text())
    assert {"stage", "seconds"} <= set(written[0].keys())
    assert any(r["stage"] == "write_outputs" for r in written)


# ---------- Neutral report language ----------

def test_report_language_is_neutral(tmp_path):
    run_pipeline(
        file_path="examples/sample_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path,
    )
    md = (tmp_path / "report.md").read_text()
    assert "Minimal inconsistent sets" in md
    assert "cannot all be true" in md
    assert "does not determine which" in md
    assert "violated" not in md
    tree = (tmp_path / "theory_tree.txt").read_text()
    assert "INCOMPATIBLE WITH" in tree


# ---------- Extra parser / solver / verbalizer / gate coverage ----------

def test_parser_iff_entailment():
    import z3
    from src.fol_parser import Env

    env = Env()
    ax, _ = parse_fol("forall x. (Glowing(x) <-> Hot(x))", env)
    fact, _ = parse_fol("Glowing(coal)", env)
    goal, _ = parse_fol("Hot(coal)", env)
    s = z3.Solver()
    s.add(ax, fact, z3.Not(goal))
    assert s.check() == z3.unsat


def test_solver_binary_predicates():
    props = [
        _accepted("a1", StatementType.AXIOM, "forall x, y. (Parent(x, y) -> Ancestor(x, y))"),
        _accepted("a2", StatementType.AXIOM, "Parent(anna, ben)"),
        _accepted("c1", StatementType.DERIVED_CLAIM, "Ancestor(anna, ben)"),
    ]
    verify(props)
    assert {p.id: p.verdict for p in props}["c1"] == Verdict.ENTAILED


def test_verbalizer_exists_and_iff():
    text = verbalize("exists x, y. (Loves(x, y))")
    assert "there is some x and y" in text
    text2 = verbalize("P(a) <-> Q(a)")
    assert "if and only if" in text2


def test_gate_flags_genuine_ambiguity():
    v = Vocabulary()
    s = _stmt("g1", StatementType.AXIOM, "Every bank is safe.", "Every bank is safe.")
    # the rule path reads it universally; a hypothetical LLM reads it existentially:
    prop = run_gate(s, "exists x. (Bank(x) and Safe(x))", v)
    assert prop.status == GateOutcome.AMBIGUOUS
    assert "ambiguity" in prop.gate_reason
