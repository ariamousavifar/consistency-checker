"""Semantic deduplication: over-extraction produces near-duplicate sentences
whose FOL is identical (modulo bound-variable names). They must collapse to one
node so the solver can't manufacture spurious 'X proved from X' derivation edges.
"""
from src.schema import Proposition, StatementType, GateOutcome
from src.solver import _alpha_key, mark_duplicate_fols


def _p(pid, fol, type_=StatementType.DERIVED_CLAIM, status=GateOutcome.ACCEPTED):
    return Proposition(id=pid, type=type_, original_text=pid, decontextualized=pid,
                       fol=fol, status=status, confidence=1.0)


def test_alpha_key_collapses_bound_variable_renames():
    assert _alpha_key("forall s. (not P(s) -> not Q(s))") == \
        _alpha_key("forall x. (not P(x) -> not Q(x))")
    assert _alpha_key("A(c) and B(c)") == _alpha_key("A(c) and B(c)")


def test_alpha_key_distinguishes_real_differences():
    assert _alpha_key("forall x. (P(x) -> Q(x))") != _alpha_key("forall x. (P(x) -> R(x))")
    # a disjunction weakening is NOT a duplicate of its disjunct
    assert _alpha_key("forall x. (P(x) -> Q(x))") != \
        _alpha_key("(forall x. (P(x) -> Q(x))) or (forall x. (P(x) -> not Q(x)))")


def test_duplicates_quarantined_first_kept():
    props = [
        _p("s1", "forall s. (not UFSO(s) -> not UE(s))"),
        _p("s2", "Other(c)"),
        _p("s3", "forall x. (not UFSO(x) -> not UE(x))"),   # alpha-dup of s1
        _p("s4", "UseFreeWill(crusoe) and OwnSelf(crusoe)"),
        _p("s5", "UseFreeWill(crusoe) and OwnSelf(crusoe)"),  # exact dup of s4
    ]
    n = mark_duplicate_fols(props)
    by = {p.id: p for p in props}
    assert n == 2
    assert by["s1"].status == GateOutcome.ACCEPTED      # canonical kept
    assert by["s3"].status == GateOutcome.QUARANTINED and "duplicate of s1" in by["s3"].gate_reason
    assert by["s5"].status == GateOutcome.QUARANTINED and "duplicate of s4" in by["s5"].gate_reason
    assert by["s2"].status == GateOutcome.ACCEPTED      # distinct untouched


def test_bridges_are_never_deduped():
    props = [
        _p("s1", "RaiseTax(author)"),
        _p("b1", "RaiseTax(author)", type_=StatementType.BRIDGE),  # same FOL, but a bridge
    ]
    mark_duplicate_fols(props)
    assert all(p.status == GateOutcome.ACCEPTED for p in props)
