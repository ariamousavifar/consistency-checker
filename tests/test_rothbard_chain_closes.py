"""Regression: Rothbard's self-ownership spine closes under the EXISTING solver.

Hand-encodes the s11 -> reduction -> s16 -> s24 -> s25 chain as FOL and runs it
through solver.verify(). This is the proof that the LOGIC is not the bottleneck:
given clean, well-typed, vocabulary-aligned input, Z3 derives the conclusions.

It also documents the shape that matters: the ethical spine is MONADIC -- it
needs conditionals with disjunctive consequents and consistent predicate naming,
NOT relational (EPR) predicates. EPR is only needed for the *sub*-arguments
(G owns R), which the abstraction PartialOwnership(x) folds away.

Predicate reading (domain x = a society / political-ethical order):
  FullSelfOwnership(x)       -- x grants 100% self-ownership to every man
  UniversalOtherOwnership(x) -- x grants everyone an equal share in everyone (alt 1)
  PartialOwnership(x)        -- x is rule of one class over another (alt 2)
  UniversalEthic(x)          -- x's ethic applies to every man (is universal)
  ViableEthic(x)             -- x is a viable political ethic for mankind
"""
from src.schema import Proposition, StatementType, GateOutcome, Verdict
from src.solver import verify


def _p(pid, type_, fol, text):
    return Proposition(
        id=pid, type=type_, original_text=text, decontextualized=text,
        fol=fol, status=GateOutcome.ACCEPTED, confidence=1.0,
    )


def _chain():
    AX = StatementType.AXIOM
    CL = StatementType.DERIVED_CLAIM
    return [
        _p("a1", AX,
           "forall x. (not FullSelfOwnership(x) -> (UniversalOtherOwnership(x) or PartialOwnership(x)))",
           "if not full self-ownership, then either universal other-ownership or partial (class) ownership"),
        _p("a2", AX,
           "forall x. (UniversalOtherOwnership(x) -> PartialOwnership(x))",
           "universal other-ownership is Utopian and reduces in practice to partial rule by a class"),
        _p("a3", AX,
           "forall x. (PartialOwnership(x) -> not UniversalEthic(x))",
           "class rule treats class R as subhuman -> not a universal ethic"),
        _p("a4", AX,
           "forall x. (ViableEthic(x) -> UniversalEthic(x))",
           "the elemental rule: a viable political ethic must apply to every man"),
        _p("s24", CL,
           "forall x. (not FullSelfOwnership(x) -> not UniversalEthic(x))",
           "therefore any society without full self-ownership cannot have a universal ethic"),
        _p("s25", CL,
           "forall x. (ViableEthic(x) -> FullSelfOwnership(x))",
           "thus 100% self-ownership is the only viable political ethic"),
    ]


def test_spine_conclusions_are_entailed():
    props = _chain()
    verify(props, timeout_ms=8000, effort=1)
    by_id = {p.id: p for p in props}
    assert by_id["s24"].verdict == Verdict.ENTAILED
    assert by_id["s25"].verdict == Verdict.ENTAILED


def test_layered_support_forms_a_tree_not_a_fan():
    props = _chain()
    verify(props, timeout_ms=8000, effort=1)
    by_id = {p.id: p for p in props}
    # s24 follows from the dichotomy + reduction + class-rule (all axioms).
    assert set(by_id["s24"].support) == {"a1", "a2", "a3"}
    # s25 derives from the INTERMEDIATE THEOREM s24 plus the elemental rule a4 --
    # NOT the flat {a1,a2,a3,a4}. Layered entailment attributes support to the
    # compact theorem, so the support edges form a tree (a1,a2,a3 -> s24 -> s25)
    # instead of a fan (axioms -> every claim independently).
    assert set(by_id["s25"].support) == {"a4", "s24"}


def test_spine_is_internally_consistent():
    props = _chain()
    reports = verify(props, timeout_ms=8000, effort=1)
    assert all(r.axioms_consistent is not False for r in reports)
