"""Deterministic relational (EPR) reasoning, against the real solver -- no LLM.

These mirror the live relational examples (rel_prereq_*, rel_geo_*,
rel_genealogy_*, rel_sports_*) at the logic layer, so the reasoning is pinned
independently of translation variance. All FOL is in this project's syntax:
`and`/`not`, nested single-variable quantifiers `forall x. (forall y. (...))`.

Transitive/ordering reasoning is the relational fragment's headline capability:
a cycle in a strict order is a contradiction; a chain entails its closure;
WITHOUT a transitivity premise nothing of the sort is inferred (the round-robin
boundary).
"""
from consistency_checker.gate import is_epr_safe, max_arity
from consistency_checker.schema import Proposition, StatementType, GateOutcome, Verdict
from consistency_checker.solver import verify

_TRANS = "forall x. (forall y. (forall z. ((Before(x,y) and Before(y,z)) -> Before(x,z))))"
_IRREFLEX = "forall x. (not Before(x,x))"


def _p(pid, fol, type_=StatementType.AXIOM):
    return Proposition(id=pid, type=type_, original_text=pid, decontextualized=pid,
                       fol=fol, status=GateOutcome.ACCEPTED, confidence=1.0)


def _verdicts(props):
    verify(props, timeout_ms=8000, effort=1)
    return {p.id: p.verdict for p in props}


# --- transitivity entails closure -------------------------------------------

def test_transitivity_closes_two_hop():
    props = [_p("t", _TRANS), _p("e1", "Before(a,b)"), _p("e2", "Before(b,c)"),
             _p("q", "Before(a,c)", StatementType.DERIVED_CLAIM)]
    assert _verdicts(props)["q"] == Verdict.ENTAILED


def test_transitivity_closes_three_hop():
    props = [_p("t", _TRANS), _p("e1", "Before(a,b)"), _p("e2", "Before(b,c)"),
             _p("e3", "Before(c,d)"), _p("q", "Before(a,d)", StatementType.DERIVED_CLAIM)]
    assert _verdicts(props)["q"] == Verdict.ENTAILED


# --- cycle in a strict order is unsatisfiable -------------------------------

def test_three_cycle_is_contradiction():
    props = [_p("t", _TRANS), _p("i", _IRREFLEX),
             _p("e1", "Before(a,b)"), _p("e2", "Before(b,c)"), _p("e3", "Before(c,a)")]
    reports = verify(props, 8000, 1)
    assert any(r.axioms_consistent is False for r in reports)
    assert {p.id for p in props if p.verdict == Verdict.CONTRADICTS} == {"t", "i", "e1", "e2", "e3"}


def test_two_cycle_is_contradiction():
    props = [_p("t", _TRANS), _p("i", _IRREFLEX),
             _p("e1", "Before(a,b)"), _p("e2", "Before(b,a)")]
    assert any(r.axioms_consistent is False for r in verify(props, 8000, 1))


# --- asymmetry --------------------------------------------------------------

def test_asymmetry_contradiction():
    props = [_p("asym", "forall x. (forall y. (Beats(x,y) -> not Beats(y,x)))"),
             _p("e1", "Beats(a,b)"), _p("e2", "Beats(b,a)")]
    assert any(r.axioms_consistent is False for r in verify(props, 8000, 1))


# --- the round-robin BOUNDARY: no transitivity axiom -> no contradiction ----

def test_roundrobin_without_transitivity_stays_consistent():
    # Arsenal beat Brentford, Brentford beat Chelsea, Chelsea beat Arsenal --
    # a 3-cycle that is NOT a contradiction because 'beat' is not declared an order.
    props = [_p("e1", "Beat(arsenal,brentford)"),
             _p("e2", "Beat(brentford,chelsea)"),
             _p("e3", "Beat(chelsea,arsenal)")]
    reports = verify(props, 8000, 1)
    assert all(r.axioms_consistent is not False for r in reports)
    assert not any(p.verdict == Verdict.CONTRADICTS for p in props)


# --- negative controls: no spurious entailment ------------------------------

def test_no_closure_without_transitivity():
    props = [_p("e1", "Before(a,b)"), _p("e2", "Before(b,c)"),
             _p("q", "Before(a,c)", StatementType.DERIVED_CLAIM)]
    assert _verdicts(props)["q"] != Verdict.ENTAILED


def test_no_closure_on_a_broken_chain():
    # a->b and c->d do not entail a->d even with transitivity (no b=c bridge)
    props = [_p("t", _TRANS), _p("e1", "Before(a,b)"), _p("e2", "Before(c,d)"),
             _p("q", "Before(a,d)", StatementType.DERIVED_CLAIM)]
    assert _verdicts(props)["q"] != Verdict.ENTAILED


# --- relational reductio ----------------------------------------------------

def test_relational_reductio_refutes_negated_conclusion():
    # the theory entails Before(a,c); supposing its negation must be refuted
    props = [_p("t", _TRANS), _p("e1", "Before(a,b)"), _p("e2", "Before(b,c)"),
             _p("h", "not Before(a,c)", StatementType.HYPOTHETICAL)]
    verify(props, 8000, 1)
    assert {p.id: p.verdict for p in props}["h"] == Verdict.REFUTED


# --- EPR shape on relational formulas ---------------------------------------

def test_ternary_relation_is_epr_safe():
    # EPR (Bernays-Schoenfinkel) has NO arity limit: a ternary relation is fine.
    fol = "forall x. (forall y. (forall z. (Between(x,y,z) -> Ordered(x,z))))"
    assert max_arity(fol) == 3
    assert is_epr_safe(fol)


def test_relational_role_restriction_is_not_epr_safe():
    assert not is_epr_safe("forall x. (Course(x) -> exists y. (Course(y) and Prereq(y,x)))")
