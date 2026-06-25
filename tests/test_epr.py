"""EPR (relational) fragment: binary relations enter the solver, and the one
shape that leaves the decidable Bernays-Schoenfinkel class -- a relational
forall/exists role restriction -- is set aside for description logic.
"""
from src.gate import is_epr_safe, max_arity, run_gate
from src.schema import ExtractedStatement, GateOutcome, StatementType, Verdict
from src.solver import verify
from src.vocabulary import Vocabulary
from src.schema import Proposition


def _stmt(text):
    return ExtractedStatement(id="g", type=StatementType.AXIOM, original_text=text, decontextualized=text)


def _p(pid, fol, type_=StatementType.AXIOM):
    return Proposition(id=pid, type=type_, original_text=pid, decontextualized=pid,
                       fol=fol, status=GateOutcome.ACCEPTED, confidence=1.0)


# --- shape helpers ----------------------------------------------------------

def test_max_arity():
    assert max_arity("Owns(g, r)") == 2
    assert max_arity("Man(x)") == 1
    assert max_arity("forall x. (forall y. (RulesOver(x, y) -> Subhuman(y)))") == 2


def test_epr_safe_accepts_ground_and_forall_forall_and_exists_forall():
    assert is_epr_safe("Owns(g, r)")
    assert is_epr_safe("forall x. (forall y. (RulesOver(x, y) -> Subhuman(y)))")
    assert is_epr_safe("exists y. (forall x. (R(x, y)))")
    assert is_epr_safe("forall x. (Man(x) -> Mortal(x))")          # monadic, always fine


def test_epr_unsafe_for_relational_role_restriction():
    # forall ... exists over a relation = role restriction -> needs description logic
    assert not is_epr_safe("forall x. (City(x) -> exists y. (Country(y) and LocatedIn(x, y)))")


# --- gate guard -------------------------------------------------------------

def test_gate_admits_a_ground_relation():
    prop = run_gate(_stmt("G owns R."), "Owns(g, r)", Vocabulary(), allow_relations=True)
    assert prop.status == GateOutcome.ACCEPTED
    assert max_arity(prop.fol) == 2


def test_gate_sets_aside_relational_role_restriction():
    # 'that' makes the rule translator refuse, so the relational ∀∃ is the sole
    # candidate and the EPR guard must set it aside (no safe unary fallback).
    fol = "forall x. (City(x) -> exists y. (Country(y) and LocatedIn(x, y)))"
    prop = run_gate(_stmt("Anything that is a city is located in some country."),
                    fol, Vocabulary(), allow_relations=True)
    assert prop.status == GateOutcome.QUARANTINED
    assert "EPR" in prop.gate_reason or "role-restriction" in prop.gate_reason
    assert prop.quarantine_shape == "relational-role(∀∃)"


def test_relational_reading_preferred_over_unary_rule_divergence():
    # rule translator gives unary MembersOfClassR(x); LLM gives relational
    # MemberOf(x, r). Under --allow-relations the relational reading wins instead
    # of being excluded as ambiguous.
    prop = run_gate(_stmt("All members of class R are scholars."),
                    "forall x. (MemberOf(x, r) -> Scholar(x))", Vocabulary(), allow_relations=True)
    assert prop.status == GateOutcome.ACCEPTED
    assert max_arity(prop.fol) == 2
    assert "relational" in prop.gate_reason


def test_gate_guard_is_off_without_the_flag():
    # without --allow-relations the EPR guard does not fire (behavior unchanged)
    fol = "forall x. (City(x) -> exists y. (Country(y) and LocatedIn(x, y)))"
    prop = run_gate(_stmt("Anything that is a city is located in some country."), fol, Vocabulary())
    assert "role-restriction" not in (prop.gate_reason or "")


# --- relational reasoning the unary fragment could not do -------------------

def test_relational_contradiction_is_caught():
    # G rules over R; whoever rules over a class denies it full self-ownership;
    # yet R has full self-ownership -> the three cannot all hold.
    props = [
        _p("a1", "RulesOver(g, r)"),
        _p("a2", "forall x. (forall y. (RulesOver(x, y) -> not FullSelfOwnership(y)))"),
        _p("a3", "FullSelfOwnership(r)"),
    ]
    reports = verify(props, timeout_ms=8000, effort=1)
    assert any(r.axioms_consistent is False for r in reports)
    contradicted = {p.id for p in props if p.verdict == Verdict.CONTRADICTS}
    assert contradicted == {"a1", "a2", "a3"}


def test_relational_entailment_closes():
    # every member of class R is a scholar; Aldous is a member of R -> scholar.
    props = [
        _p("a1", "forall x. (MemberOf(x, r) -> Scholar(x))"),
        _p("a2", "MemberOf(aldous, r)"),
        _p("c1", "Scholar(aldous)", type_=StatementType.DERIVED_CLAIM),
    ]
    verify(props, timeout_ms=8000, effort=1)
    by = {p.id: p for p in props}
    assert by["c1"].verdict == Verdict.ENTAILED
    assert set(by["c1"].support) == {"a1", "a2"}
