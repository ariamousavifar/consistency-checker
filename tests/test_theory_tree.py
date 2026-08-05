"""Layered entailment must reconstruct a DERIVATION TREE, not a flat fan.

The quadrilateral hierarchy is the canonical case: each theorem is provable
from the axioms alone (entailment is transitive), so a naive 'check against the
axioms' attributes every theorem directly to the axioms -- a fan. Layered
entailment instead attributes each theorem to the compact INTERMEDIATE theorem
that packages the axioms, yielding the chain a1,a2 -> T1 -> T2 -> T3.
"""
from consistency_checker.schema import Proposition, StatementType, GateOutcome, Verdict
from consistency_checker.solver import verify


def _p(pid, type_, fol):
    return Proposition(id=pid, type=type_, original_text=pid, decontextualized=pid,
                       fol=fol, status=GateOutcome.ACCEPTED, confidence=1.0)


def _hierarchy():
    AX, CL = StatementType.AXIOM, StatementType.DERIVED_CLAIM
    return [
        _p("a1", AX, "forall x. (Square(x) -> Rectangle(x))"),
        _p("a2", AX, "forall x. (Rectangle(x) -> Parallelogram(x))"),
        _p("a3", AX, "forall x. (Parallelogram(x) -> Quadrilateral(x))"),
        _p("a4", AX, "forall x. (Quadrilateral(x) -> Polygon(x))"),
        _p("t1", CL, "forall x. (Square(x) -> Parallelogram(x))"),
        _p("t2", CL, "forall x. (Square(x) -> Quadrilateral(x))"),
        _p("t3", CL, "forall x. (Square(x) -> Polygon(x))"),
    ]


def test_hierarchy_all_theorems_entailed():
    props = _hierarchy()
    verify(props, timeout_ms=8000, effort=1)
    by_id = {p.id: p for p in props}
    assert all(by_id[t].verdict == Verdict.ENTAILED for t in ("t1", "t2", "t3"))


def test_hierarchy_support_chains_through_theorems():
    props = _hierarchy()
    verify(props, timeout_ms=8000, effort=1)
    by_id = {p.id: p for p in props}
    # t1 from the two base axioms; t2 from t1 (+a3); t3 from t2 (+a4) -- a chain.
    assert set(by_id["t1"].support) == {"a1", "a2"}
    assert set(by_id["t2"].support) == {"t1", "a3"}
    assert set(by_id["t3"].support) == {"t2", "a4"}
    # i.e. NOT the flat fan {a1,a2,a3} / {a1,a2,a3,a4}
    assert by_id["t3"].support != ["a1", "a2", "a3", "a4"]
