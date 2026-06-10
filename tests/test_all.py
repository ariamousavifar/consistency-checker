"""Offline test suite: no network, no API keys. Run with `pytest`."""
from __future__ import annotations

import z3

from src.fidelity import fidelity_check
from src.fol_parser import Env, check_equivalence, parse_fol
from src.gate import run_gate
from src.pipeline import run_pipeline
from src.rule_translator import rule_translate
from src.schema import ExtractedStatement, GateOutcome, StatementType, Verdict
from src.solver import verify
from src.verbalizer import verbalize
from src.vocabulary import Vocabulary


# ---------- FOL parser ----------

def test_parser_syllogism_entailment():
    env = Env()
    ax1, _ = parse_fol("forall x. (Human(x) -> Mortal(x))", env)
    ax2, _ = parse_fol("Human(socrates)", env)
    goal, _ = parse_fol("Mortal(socrates)", env)
    s = z3.Solver()
    s.add(ax1, ax2, z3.Not(goal))
    assert s.check() == z3.unsat


def test_parser_connectives_and_quantifiers():
    f, _ = parse_fol("forall x, y. (Loves(x, y) -> not Hates(x, y))")
    assert f is not None
    g, _ = parse_fol("exists x. (P(x) and (Q(x) or not R(x))) <-> S(a)")
    assert g is not None


def test_parser_rejects_garbage():
    import pytest
    from src.fol_parser import FOLParseError

    for bad in ["forall x (P(x))", "P(x", "and P(a)", "Mortal"]:
        with pytest.raises(FOLParseError):
            parse_fol(bad)


# ---------- Equivalence ----------

def test_equivalence_detects_same_meaning():
    assert check_equivalence(
        "forall x. (Human(x) -> Mortal(x))",
        "forall y. (not Human(y) or Mortal(y))",
    ) == "equivalent"


def test_equivalence_detects_divergence():
    assert check_equivalence(
        "forall x. (Human(x) -> Mortal(x))",
        "forall x. (Mortal(x) -> Human(x))",
    ) == "divergent"


# ---------- Rule translator ----------

def test_rule_translator_fragment():
    v = Vocabulary()
    assert rule_translate("All philosophers are human.", v) == "forall x. (Philosopher(x) -> Human(x))"
    assert rule_translate("Socrates is a philosopher.", v) == "Philosopher(socrates)"
    assert rule_translate("Socrates is not mortal.", v) == "not Mortal(socrates)"
    assert rule_translate("Some seekers of truth are never satisfied.", v) == (
        "exists x. (SeekerOfTruth(x) and NeverSatisfied(x))"
    )
    assert rule_translate("No politician is honest.", v) == "forall x. (Politician(x) -> not Honest(x))"


def test_rule_translator_refuses_outside_fragment():
    v = Vocabulary()
    assert rule_translate("Every person who questions their own beliefs is a seeker of truth.", v) is None
    assert rule_translate("If it rains then the ground is wet.", v) is None
    assert rule_translate("Socrates probably enjoyed wine.", v) is None


# ---------- Vocabulary normalization ----------

def test_vocabulary_merges_plural_variants():
    v = Vocabulary()
    a = v.normalize_fol("forall x. (Humans(x) -> Mortals(x))")
    b = v.normalize_fol("Human(socrates)")
    assert "Humans(" in a and "Humans(socrates)" in b  # first-seen form wins for both


# ---------- Verbalizer & fidelity ----------

def test_verbalizer_roundtrip_readable():
    text = verbalize("forall x. (Philosopher(x) -> Mortal(x))")
    assert "for every x" in text and "philosopher" in text and "mortal" in text


def test_fidelity_rejects_invented_predicates():
    good = fidelity_check("Mortal(socrates)", "Socrates is mortal.")
    bad = fidelity_check("Banker(socrates)", "Socrates is mortal.")
    assert good.passed and not bad.passed


# ---------- Gate ----------

def _stmt(id, type, text, decon):
    return ExtractedStatement(id=id, type=type, original_text=text, decontextualized=decon)


def test_gate_accepts_agreeing_candidates_with_high_confidence():
    v = Vocabulary()
    s = _stmt("a1", StatementType.AXIOM, "Every human is mortal.", "Every human is mortal.")
    prop = run_gate(s, "forall y. (Human(y) -> Mortal(y))", v)
    assert prop.status == GateOutcome.ACCEPTED and prop.confidence >= 0.9


def test_gate_quarantines_unfaithful_llm_translation():
    v = Vocabulary()
    s = _stmt("a2", StatementType.AXIOM, "x", "Socrates questions everything he hears.")
    prop = run_gate(s, "Banker(plato)", v)  # rule refuses; LLM hallucinated
    assert prop.status == GateOutcome.QUARANTINED


def test_gate_quarantines_non_propositional():
    v = Vocabulary()
    s = _stmt("a3", StatementType.NON_PROPOSITIONAL, "His ideas soar.", "His ideas soar.")
    prop = run_gate(s, None, v)
    assert prop.status == GateOutcome.QUARANTINED


# ---------- Solver ----------

from src.schema import Proposition


def _accepted(id, type, fol):
    return Proposition(
        id=id, type=type, original_text=id, decontextualized=id, fol=fol,
        status=GateOutcome.ACCEPTED, confidence=0.9,
    )


def test_solver_verdicts_and_minimal_conflict():
    props = [
        _accepted("ax1", StatementType.AXIOM, "forall x. (Philosopher(x) -> Human(x))"),
        _accepted("ax2", StatementType.AXIOM, "forall x. (Human(x) -> Mortal(x))"),
        _accepted("ax3", StatementType.AXIOM, "Philosopher(socrates)"),
        _accepted("ax4", StatementType.AXIOM, "forall x. (Cat(x) -> Animal(x))"),  # irrelevant axiom
        _accepted("c1", StatementType.DERIVED_CLAIM, "Mortal(socrates)"),
        _accepted("c2", StatementType.DERIVED_CLAIM, "not Mortal(socrates)"),
        _accepted("c3", StatementType.DERIVED_CLAIM, "exists x. (Human(x) and Wise(x))"),
    ]
    verify(props)
    by_id = {p.id: p for p in props}
    assert by_id["c1"].verdict == Verdict.ENTAILED
    assert set(by_id["c1"].support) == {"ax1", "ax2", "ax3"}
    assert by_id["c2"].verdict == Verdict.CONTRADICTS
    assert set(by_id["c2"].conflict) == {"ax1", "ax2", "ax3"}  # ax4 must not appear: minimality
    assert by_id["c3"].verdict == Verdict.NOT_ENTAILED


def test_solver_detects_inconsistent_axiom_set():
    props = [
        _accepted("ax1", StatementType.AXIOM, "forall x. (Bird(x) -> Flies(x))"),
        _accepted("ax2", StatementType.AXIOM, "Bird(tweety)"),
        _accepted("ax3", StatementType.AXIOM, "not Flies(tweety)"),
        _accepted("c1", StatementType.DERIVED_CLAIM, "Flies(tweety)"),
    ]
    reports = verify(props)
    assert any(r.axioms_consistent is False for r in reports)
    bad = next(r for r in reports if r.axioms_consistent is False)
    assert set(bad.axiom_conflict) == {"ax1", "ax2", "ax3"}
    assert {p.id: p.verdict for p in props}["c1"] == Verdict.UNKNOWN


# ---------- End-to-end offline pipeline ----------

def test_pipeline_offline_end_to_end(tmp_path):
    report = run_pipeline(
        file_path="examples/sample_essay.txt",
        offline=True,
        fixtures_dir="examples/fixtures",
        out_dir=tmp_path,
    )
    by_id = {p.id: p for p in report.propositions}
    assert by_id["s6"].verdict == Verdict.ENTAILED
    assert set(by_id["s6"].support) == {"s1", "s2", "s3"}
    assert by_id["s7"].verdict == Verdict.ENTAILED
    assert set(by_id["s7"].support) == {"s3", "s4", "s5"}
    assert by_id["s8"].verdict == Verdict.CONTRADICTS
    assert set(by_id["s8"].conflict) == {"s3", "s4", "s5"}
    assert by_id["s10"].verdict == Verdict.NOT_ENTAILED
    assert by_id["s9"].status == GateOutcome.QUARANTINED
    assert by_id["s9"].verdict is None
    # provenance: every located statement maps back into the source file
    raw = open("examples/sample_essay.txt", encoding="utf-8").read()
    for p in report.propositions:
        if p.span:
            assert 0 <= p.span.start < p.span.end <= len(raw)
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "store.json").exists()


# ---------- Theory tree / graph rendering ----------

def test_tree_dot_svg_outputs(tmp_path):
    from src.tree_builder import build_dot, build_svg, build_tree_text

    report = run_pipeline(
        file_path="examples/sample_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path,
    )
    tree = build_tree_text(report)
    assert "theory cluster 0" in tree
    assert "axioms consistent: YES" in tree
    assert "CONFLICTS WITH" in tree            # s8 shows its conflict set inline
    assert "excluded from theory" in tree      # s9 appears, never silently dropped

    dot = build_dot(report)
    assert dot.startswith("digraph")
    assert 's3 -> s8' in dot or 's8 -> s3' in dot   # conflict edge present
    assert '#a32d2d' in dot and '#cfe0f5' in dot    # red conflict + blue axiom colors

    svg = build_svg(report)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    for sid in ("s1", "s6", "s8", "s9", "s10"):
        assert sid in svg
    assert "stroke-dasharray" in svg                 # dashed conflict edge / excluded node

    for fname in ("theory_tree.txt", "graph.dot", "graph.svg"):
        assert (tmp_path / fname).exists()
