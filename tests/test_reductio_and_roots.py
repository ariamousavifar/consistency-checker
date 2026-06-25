"""Two solver capabilities for reconstructing real arguments:

  #2 asserted-premise roots: a premise the author asserts (typed derived_claim,
     not axiom) is promoted to a root so the claims that follow from it derive.
  reductio: a hypothetical supposition that contradicts the established theory is
     a successful reductio ad absurdum (its negation is proven), reported as
     REFUTED -- never as the author contradicting himself.
"""
from src.schema import Proposition, StatementType, GateOutcome, Verdict
from src.solver import verify


def _p(pid, type_, fol):
    return Proposition(id=pid, type=type_, original_text=pid, decontextualized=pid,
                       fol=fol, status=GateOutcome.ACCEPTED, confidence=1.0)


# --- #2 asserted-premise roots ----------------------------------------------

def test_asserted_premise_becomes_a_root_and_its_consequent_derives():
    AX, CL = StatementType.AXIOM, StatementType.DERIVED_CLAIM
    props = [
        _p("a1", AX, "Man(socrates)"),
        _p("p1", CL, "forall x. (Man(x) -> Mortal(x))"),   # asserted premise, typed claim
        _p("c1", CL, "Mortal(socrates)"),                   # follows from a1 + p1
    ]
    verify(props, timeout_ms=8000, effort=1)
    by = {p.id: p for p in props}
    # the universal premise isn't derivable from a single instance -> it's a root
    assert by["p1"].verdict == Verdict.NOT_ENTAILED
    assert "asserted premise" in by["p1"].gate_reason
    # the conclusion now derives from the axiom + the asserted premise
    assert by["c1"].verdict == Verdict.ENTAILED
    assert set(by["c1"].support) == {"a1", "p1"}


# --- reductio ad absurdum ----------------------------------------------------

def test_supposition_contradicting_theory_is_refuted():
    AX, HY = StatementType.AXIOM, StatementType.HYPOTHETICAL
    props = [
        _p("a1", AX, "forall x. (Square(x) -> ClosedFigure(x))"),
        _p("a2", AX, "Square(s)"),
        _p("h1", HY, "not ClosedFigure(s)"),   # suppose the opposite of the conclusion
    ]
    verify(props, timeout_ms=8000, effort=1)
    by = {p.id: p for p in props}
    assert by["h1"].verdict == Verdict.REFUTED
    assert "reductio" in by["h1"].gate_reason
    assert set(by["h1"].conflict) == {"a1", "a2"}
    # the supposition must NOT poison the asserted theory's consistency
    assert all(r.axioms_consistent is not False for r in verify(props, 8000, 1))


def test_consistent_supposition_is_not_refuted():
    AX, HY = StatementType.AXIOM, StatementType.HYPOTHETICAL
    props = [
        _p("a1", AX, "forall x. (Square(x) -> ClosedFigure(x))"),
        _p("h1", HY, "Triangle(t)"),   # consistent with the theory; a case split
    ]
    verify(props, timeout_ms=8000, effort=1)
    by = {p.id: p for p in props}
    assert by["h1"].verdict == Verdict.NOT_ENTAILED
    assert "consistent with the theory" in by["h1"].gate_reason
