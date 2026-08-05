"""Forward-chaining refutation prover: reconstruct the DERIVATION that reaches a
contradiction, so an inconsistent argument shows its colliding chains instead of
a flat unsat core.
"""
from dataclasses import dataclass

from consistency_checker.forward_chain import Lit, explain, parse_clause, render_text, serialize


@dataclass
class P:
    id: str
    fol: str


def _props(d):
    return [P(k, v) for k, v in d.items()]


# --- clause recognition -----------------------------------------------------

def test_parse_ground_fact():
    kind, lit = parse_clause("Ally(atlas, brava)", "s1")
    assert kind == "fact" and lit == Lit(False, "Ally", ("atlas", "brava"))


def test_parse_negated_fact():
    kind, lit = parse_clause("not Trust(atlas, diaz)", "s")
    assert kind == "fact" and lit.neg and lit.pred == "Trust"


def test_parse_transitivity_rule():
    kind, rule = parse_clause(
        "forall x. (forall y. (forall z. ((Before(x, y) and Before(y, z)) -> Before(x, z))))", "t")
    assert kind == "rule"
    assert rule.vars == frozenset({"x", "y", "z"})
    assert len(rule.body) == 2 and not rule.head.neg


def test_parse_negative_bridge():
    kind, rule = parse_clause("forall x. (forall y. (Rival(x, y) -> not Trust(x, y)))", "b")
    assert kind == "rule" and rule.head.neg and rule.head.pred == "Trust"


def test_parse_irreflexivity_is_bare_universal():
    kind, rule = parse_clause("forall x. (not Before(x, x))", "i")
    assert kind == "rule" and rule.body == () and rule.head.neg


def test_unsupported_shapes_return_none():
    assert parse_clause("forall x. (P(x) or Q(x))", "s") is None       # disjunction
    assert parse_clause("exists x. (P(x))", "s") is None               # existential


# --- refutation reconstruction ----------------------------------------------

def test_two_branch_collision_rebuilds_both_chains():
    ref = explain(_props({
        "s1": "Ally(atlas, brava)", "s2": "Ally(brava, cruz)", "s3": "Ally(cruz, diaz)",
        "s4": "forall x. (forall y. (forall z. ((Ally(x, y) and Ally(y, z)) -> Ally(x, z))))",
        "s5": "forall x. (forall y. (Ally(x, y) -> Trust(x, y)))",
        "s6": "Rival(atlas, echo)", "s7": "Rival(echo, felix)", "s8": "Rival(felix, diaz)",
        "s9": "forall x. (forall y. (forall z. ((Rival(x, y) and Rival(y, z)) -> Rival(x, z))))",
        "s10": "forall x. (forall y. (Rival(x, y) -> not Trust(x, y)))",
    }))
    assert ref is not None
    labels = {str(ref.left), str(ref.right)}
    assert labels == {"Trust(atlas, diaz)", "not Trust(atlas, diaz)"}
    # the intermediate theorems were reconstructed, not just the clashing tips
    derived = {s.lit for s in ref.steps.values() if s.premises}
    assert Lit(False, "Ally", ("atlas", "diaz")) in derived
    assert Lit(False, "Rival", ("atlas", "diaz")) in derived


def test_transitive_cycle_refutation():
    # a -> b -> c -> a with transitivity + irreflexivity derives Before(a,a)
    ref = explain(_props({
        "e1": "Before(a, b)", "e2": "Before(b, c)", "e3": "Before(c, a)",
        "t": "forall x. (forall y. (forall z. ((Before(x, y) and Before(y, z)) -> Before(x, z))))",
        "i": "forall x. (not Before(x, x))",
    }))
    assert ref is not None
    preds = {ref.left.pred, ref.right.pred}
    assert preds == {"Before"}
    # one side is a self-loop derived by transitivity
    assert any(s.lit.args[0] == s.lit.args[1] for s in ref.steps.values())


def test_consistent_set_has_no_refutation():
    ref = explain(_props({
        "s1": "Ally(atlas, brava)", "s2": "Ally(brava, cruz)",
        "s3": "forall x. (forall y. (forall z. ((Ally(x, y) and Ally(y, z)) -> Ally(x, z))))",
        "s4": "forall x. (forall y. (Ally(x, y) -> Trust(x, y)))",
    }))
    assert ref is None


def test_full_derivation_keeps_more_than_pruned():
    ref = explain(_props({
        "s1": "Ally(atlas, brava)", "s2": "Ally(brava, cruz)", "s3": "Ally(cruz, diaz)",
        "s4": "forall x. (forall y. (forall z. ((Ally(x, y) and Ally(y, z)) -> Ally(x, z))))",
        "s5": "forall x. (forall y. (Ally(x, y) -> Trust(x, y)))",
        "s6": "Rival(atlas, echo)", "s7": "Rival(echo, felix)", "s8": "Rival(felix, diaz)",
        "s9": "forall x. (forall y. (forall z. ((Rival(x, y) and Rival(y, z)) -> Rival(x, z))))",
        "s10": "forall x. (forall y. (Rival(x, y) -> not Trust(x, y)))",
    }))
    pruned = serialize(ref, prune=True)
    full = serialize(ref, prune=False)
    # full closure derives off-path facts (Trust(brava, cruz), etc.) that pruning drops
    assert len(full["nodes"]) > len(pruned["nodes"])
    # both still name the same two clashing tips
    assert (full["left_label"], full["right_label"]) == (pruned["left_label"], pruned["right_label"])


def test_serialize_and_text_render():
    ref = explain(_props({
        "a": "Governs(atlas, diaz)",
        "b": "forall x. (forall y. (Governs(x, y) -> Accountable(x, y)))",
        "c": "not Accountable(atlas, diaz)",
    }))
    assert ref is not None
    s = serialize(ref)
    assert s["left_label"] and s["right_label"] and s["nodes"]
    txt = render_text(ref)
    assert "CONTRADICTION" in txt and "Accountable(atlas, diaz)" in txt
