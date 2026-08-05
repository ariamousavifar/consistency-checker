"""v0.4 feature tests: lemmatization merge, compound splitting, symmetric
consistency edge cases, effort-3 cross-cluster sweep, solver instrumentation,
incomplete-argument reporting, and the all-examples manifest. All offline."""
from __future__ import annotations

import json

from consistency_checker.extraction import apply_compound_splitting
from consistency_checker.lemmatizer import lemma
from consistency_checker.pipeline import run_pipeline
from consistency_checker.schema import ExtractedStatement, GateOutcome, Proposition, StatementType, Verdict
from consistency_checker.solver import verify
from consistency_checker.splitter import split_statement
from consistency_checker.vocabulary import Vocabulary


def _accepted(id, type, fol):
    return Proposition(
        id=id, type=type, original_text=id, decontextualized=id, fol=fol,
        status=GateOutcome.ACCEPTED, confidence=0.9,
    )


# ---------- Lemmatization (item 12) ----------

def test_lemma_merges_nominalizations():
    assert lemma("taxation") == lemma("taxes") == lemma("tax")
    assert lemma("mortality") == lemma("mortal")
    assert lemma("happiness") == lemma("happy")
    assert lemma("equality") == lemma("equal")


def test_lemma_leaves_short_words_alone():
    for w in ["tax", "theft", "state", "road", "idea", "art", "age"]:
        assert lemma(w) == w


def test_vocabulary_merges_tax_and_taxation():
    v = Vocabulary()
    a = v.normalize_fol("forall x. (Taxation(x) -> Theft(x))")
    b = v.normalize_fol("exists x. (Tax(x) and Good(x))")
    # both predicates collapse to one canonical symbol
    assert "Taxation(" in a and "Taxation(" in b
    assert "Tax(" not in b.replace("Taxation(", "")


def test_taxation_contradiction_now_fires_with_lemmatization():
    """The live-run bug: Tax vs Taxation kept the contradiction invisible.
    With lemmatization + the bridge, the inconsistency must surface."""
    v = Vocabulary()
    props = [
        _accepted("t1", StatementType.AXIOM, v.normalize_fol("forall x. (Taxation(x) -> Theft(x))")),
        _accepted("b1", StatementType.BRIDGE, v.normalize_fol("forall x. (Theft(x) -> not MorallyJustified(x))")),
        _accepted("t3", StatementType.DERIVED_CLAIM, v.normalize_fol("exists x. (Tax(x) and MorallyJustified(x))")),
    ]
    verify(props)
    by_id = {p.id: p for p in props}
    assert by_id["t3"].verdict == Verdict.CONTRADICTS
    assert {"t1", "b1"} <= set(by_id["t3"].conflict) | {"t3"}


# ---------- Compound splitting (item 13) ----------

def test_splitter_splits_clause_conjunction():
    out = split_statement("Socrates was a philosopher, and all philosophers are human.")
    assert len(out) == 2
    assert "philosopher" in out[0].lower()
    assert "human" in out[1].lower()


def test_splitter_preserves_noun_phrase_conjunction():
    out = split_statement("Roads and hospitals do not pay for themselves.")
    assert len(out) == 1  # must NOT split a shared-predicate noun phrase


def test_splitter_leaves_simple_statements():
    assert split_statement("Every human is mortal.") == ["Every human is mortal."]


def test_apply_compound_splitting_assigns_child_ids():
    stmts = [ExtractedStatement(
        id="s3", type=StatementType.AXIOM,
        original_text="Socrates was a philosopher, and all philosophers are human.",
        decontextualized="Socrates was a philosopher, and all philosophers are human.",
    )]
    out = apply_compound_splitting(stmts)
    assert [s.id for s in out] == ["s3.1", "s3.2"]
    assert all(s.type == StatementType.AXIOM for s in out)


# ---------- Symmetric consistency (item 9) ----------

def test_two_contradicting_axioms_flag_the_set():
    props = [
        _accepted("a1", StatementType.AXIOM, "Round(earth)"),
        _accepted("a2", StatementType.AXIOM, "not Round(earth)"),
    ]
    reports = verify(props)
    assert reports[0].axioms_consistent is False
    assert set(reports[0].axiom_conflict) == {"a1", "a2"}


def test_claim_contradicting_claim_is_caught_regardless_of_type():
    """Neither statement is an axiom; the contradiction must still be found."""
    props = [
        _accepted("c1", StatementType.DERIVED_CLAIM, "Guilty(defendant)"),
        _accepted("c2", StatementType.DERIVED_CLAIM, "not Guilty(defendant)"),
    ]
    verify(props)
    by_id = {p.id: p for p in props}
    assert by_id["c1"].verdict == Verdict.CONTRADICTS
    assert by_id["c2"].verdict == Verdict.CONTRADICTS


def test_consistent_set_still_classifies_entailment():
    props = [
        _accepted("a1", StatementType.AXIOM, "forall x. (Dog(x) -> Mammal(x))"),
        _accepted("a2", StatementType.AXIOM, "Dog(rex)"),
        _accepted("c1", StatementType.DERIVED_CLAIM, "Mammal(rex)"),
        _accepted("c2", StatementType.DERIVED_CLAIM, "Friendly(rex)"),
    ]
    verify(props)
    by_id = {p.id: p for p in props}
    assert by_id["c1"].verdict == Verdict.ENTAILED
    assert by_id["c2"].verdict == Verdict.NOT_ENTAILED


# ---------- Effort 3 cross-cluster sweep (item 8b) ----------

def test_effort_three_finds_cross_cluster_conflict():
    # Two clusters that share no predicate at effort 1, but a bridge ties them.
    props = [
        _accepted("a1", StatementType.AXIOM, "forall x. (Tax(x) -> Theft(x))"),
        _accepted("a2", StatementType.DERIVED_CLAIM, "exists x. (Tax(x) and Good(x))"),
        _accepted("br", StatementType.BRIDGE, "forall x. (Theft(x) -> not Good(x))"),
    ]
    # at effort 1 these may split; at effort 3 the sweep must find the conflict
    verify(props, effort=3)
    assert any(p.verdict == Verdict.CONTRADICTS for p in props)


# ---------- Solver instrumentation (item 6) ----------

def test_cluster_reports_carry_instrumentation():
    props = [
        _accepted("a1", StatementType.AXIOM, "forall x. (A(x) -> B(x))"),
        _accepted("a2", StatementType.AXIOM, "A(thing)"),
        _accepted("c1", StatementType.DERIVED_CLAIM, "B(thing)"),
    ]
    reports = verify(props)
    r = reports[0]
    assert r.n_statements == 3
    assert r.solver_ms >= 0.0
    assert r.hit_timeout is False


# ---------- Incomplete-argument reporting (item 10) ----------

def test_incomplete_argument_note_in_report(tmp_path):
    report = run_pipeline(
        file_path="examples/taxation_essay.txt", offline=True,
        fixtures_dir="examples/fixtures", out_dir=tmp_path,
    )
    md = (tmp_path / "report.md").read_text()
    assert "incomplete argument, not an inconsistency" in md
    by_id = {p.id: p for p in report.propositions}
    assert by_id["t3"].verdict == Verdict.NOT_ENTAILED  # plain run: no bridge


# ---------- All-examples manifest ----------

def test_examples_manifest_is_valid_and_runnable(tmp_path):
    manifest = json.loads(open("examples/examples.json").read())
    names = {e["name"] for e in manifest["examples"]}
    assert {"sample_essay", "taxation_plain", "taxation_bridged"} <= names
    # Only the original three ship with offline fixtures; the tier1/tier2 and
    # real-text examples are live-only. Run just the fixture-backed ones here.
    fixture_backed = {"sample_essay", "taxation_plain", "taxation_bridged"}
    for ex in manifest["examples"]:
        if ex["name"] not in fixture_backed:
            continue
        report = run_pipeline(
            file_path=ex["file"], offline=True, fixtures_dir="examples/fixtures",
            out_dir=tmp_path / ex["name"], bridges_path=ex.get("bridges"),
        )
        assert (tmp_path / ex["name"] / "report.md").exists()
        if ex["name"] == "taxation_bridged":
            assert any(p.verdict == Verdict.CONTRADICTS for p in report.propositions)
        if ex["name"] == "taxation_plain":
            assert all(p.verdict != Verdict.CONTRADICTS for p in report.propositions)


# ---------- Effort 0 still skips, effort regression intact ----------

def test_effort_levels_monotonic_behavior(tmp_path):
    r0 = run_pipeline("examples/sample_essay.txt", offline=True,
                      fixtures_dir="examples/fixtures", out_dir=tmp_path / "e0", effort=0)
    assert r0.clusters == []
    r1 = run_pipeline("examples/sample_essay.txt", offline=True,
                      fixtures_dir="examples/fixtures", out_dir=tmp_path / "e1", effort=1)
    assert len(r1.clusters) >= 1
