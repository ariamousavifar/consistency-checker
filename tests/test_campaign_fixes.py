"""Regression tests for the fixes from the v0.9 test campaign (N1-N24)."""
from consistency_checker.schema import GateOutcome, Proposition, StatementType, Verdict
from consistency_checker.solver import verify
from consistency_checker.tree_builder import build_tree_text
from consistency_checker.vocabulary import Vocabulary, _const_key


def _p(pid, fol, type_=StatementType.AXIOM):
    return Proposition(id=pid, type=type_, original_text=pid, decontextualized=pid,
                       fol=fol, status=GateOutcome.ACCEPTED, confidence=1.0)


# --- N15: code-constant underscore unification ------------------------------

def test_const_key_strips_underscore_in_codes():
    assert _const_key("c6_5060") == "c65060"
    assert _const_key("c6_100a") == "c6100a"
    assert _const_key("c65060") == "c65060"      # already canonical
    assert _const_key("old_ferry") == "old_ferry"  # word constant: underscore kept
    assert _const_key("socrates") == "socrates"


def test_normalize_fol_unifies_code_constant_spellings():
    v = Vocabulary()
    a = v.normalize_fol("Require(c65060, c61060)")   # batch 1 spelling
    b = v.normalize_fol("Require(c6_5060, c6_100a)")  # batch 2 spelling of same codes
    assert "c65060" in a and "c65060" in b           # 6.5060 -> one constant
    assert "c6_5060" not in b


def test_prereq_cycle_closes_despite_mixed_constant_spelling():
    # the planted edge uses the underscored spelling; without unification the
    # cycle never connects (the live false negative). With it, UNSAT.
    v = Vocabulary()
    raw = [
        "Prereq(c6100a, c6100b)", "Prereq(c6100b, c65060)",
        "forall a. (forall b. (forall c. ((Prereq(a,b) and Prereq(b,c)) -> Prereq(a,c))))",
        "forall x. (not Prereq(x,x))",
        "Prereq(c6_5060, c6_100a)",   # planted edge, underscored spelling
    ]
    props = [_p(f"s{i}", v.normalize_fol(f)) for i, f in enumerate(raw)]
    reports = verify(props, timeout_ms=8000, effort=1)
    assert any(r.axioms_consistent is False for r in reports)


# --- N1/N3: no `unknown` fall-through in an inconsistent cluster -------------

def test_bystander_axiom_and_independent_claim_not_marked_unknown():
    props = [
        _p("a1", "forall x. (P(x) -> Q(x))"),
        _p("a2", "P(a)"),
        _p("c1", "not Q(a)", StatementType.DERIVED_CLAIM),   # {a1,a2,c1} inconsistent
        _p("a3", "forall x. (Q(x) -> T(x))"),                # bystander axiom (shares Q)
        _p("c2", "T(a)", StatementType.DERIVED_CLAIM),       # independent claim
    ]
    verify(props, timeout_ms=8000, effort=1)
    by = {p.id: p for p in props}
    assert by["a1"].verdict == Verdict.CONTRADICTS
    assert by["a3"].verdict is None                 # bystander axiom -> '-', not unknown
    assert by["c2"].verdict == Verdict.NOT_ENTAILED  # independent claim -> not unknown
    assert all(p.verdict != Verdict.UNKNOWN for p in props)


# --- N4: no false pairwise INCOMPATIBLE WITH in the text tree ----------------

def test_text_tree_uses_joint_not_pairwise_conflict():
    from consistency_checker.schema import RunReport, ClusterReport
    props = [
        _p("a1", "forall x. (P(x) -> Q(x))"),
        _p("a2", "P(a)"),
        _p("c1", "not Q(a)", StatementType.DERIVED_CLAIM),
    ]
    verify(props, timeout_ms=8000, effort=1)
    rep = RunReport(source_file="x", mode="test", propositions=props,
                    clusters=[ClusterReport(cluster_id=0, statement_ids=["a1", "a2", "c1"],
                                            axioms_consistent=False, axiom_conflict=["a1", "a2", "c1"])],
                    vocabulary_predicates=[], vocabulary_constants=[])
    tree = build_tree_text(rep)
    # c1 is a claim in conflict with no forward-chain refutation attached here ->
    # a single joint line, never N pairwise "INCOMPATIBLE WITH" edges.
    assert "jointly inconsistent with the set" in tree
    assert "INCOMPATIBLE WITH" not in tree
