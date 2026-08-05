"""v0.8: pluggable semantic (NLI) judge + its use in the fidelity check.

These tests use stub judges/clients only -- no network. They prove the wiring
(parsing, caching, fallback) and that the lexical path is untouched when no judge
is supplied, which is what keeps the rest of the suite deterministic.
"""
from consistency_checker.fidelity import fidelity_check
from consistency_checker.semantics import LLMJudge, SemanticJudge


class FakeClient:
    """Stand-in for LLMClient.complete_json: scripted replies + a call counter."""

    def __init__(self, reply):
        self._reply = reply
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        return self._reply(system, user) if callable(self._reply) else self._reply


def test_llmjudge_parses_bool_and_caches():
    client = FakeClient({"equivalent": True})
    judge = LLMJudge(client)
    assert judge.equivalent("x is a fellow", "x is a fellow of the academy") is True
    # second identical question must hit the cache, not the client
    judge.equivalent("x is a fellow", "x is a fellow of the academy")
    assert client.calls == 1


def test_llmjudge_equivalence_is_symmetric_in_cache():
    client = FakeClient({"equivalent": True})
    judge = LLMJudge(client)
    judge.equivalent("a fellow", "a fellow of the academy")
    judge.equivalent("a fellow of the academy", "a fellow")  # reversed args
    assert client.calls == 1  # symmetric key -> one call


def test_llmjudge_identity_shortcircuits():
    client = FakeClient({"equivalent": False})
    judge = LLMJudge(client)
    assert judge.equivalent("same text", "same text") is True
    assert client.calls == 0  # never asked the model


def test_llmjudge_entails_accepts_string_yes():
    client = FakeClient({"entails": "yes"})
    judge = LLMJudge(client)
    assert judge.entails("every laureate is a fellow", "laureates are fellows") is True


def test_llmjudge_returns_none_on_bad_reply():
    judge = LLMJudge(FakeClient({"unexpected": 1}))
    assert judge.equivalent("a", "b") is None
    judge2 = LLMJudge(FakeClient(lambda s, u: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert judge2.entails("a", "b") is None


def test_llmjudge_satisfies_protocol():
    assert isinstance(LLMJudge(FakeClient({})), SemanticJudge)


# --- fidelity.py is lexical-only -------------------------------------------
# The NLI judge is deliberately NOT wired into single-candidate fidelity: a live
# run showed it over-quarantined faithful-but-lossy verb-object squashes and
# slowed the gate ~10x. The judge now fires only at two-candidate adjudication
# (gate.py), so fidelity_check takes no judge argument.

class StubJudge:
    def __init__(self, decision):
        self._d = decision

    def entails(self, a, b):
        return self._d

    def equivalent(self, a, b):
        return self._d


def test_fidelity_is_lexical_only_and_takes_no_judge():
    import inspect
    assert "judge" not in inspect.signature(fidelity_check).parameters


def test_fidelity_unchanged_without_judge():
    r = fidelity_check("Fellow(aldous)", "Aldous is a fellow")
    assert r.passed and 0.0 <= r.coverage <= 1.0


# --- gate.py deterministic modifier-divergence (no LLM) ---------------------

from consistency_checker.gate import _is_modifier_variant, _modifier_only_divergence


def test_modifier_variant_detects_folded_modifier():
    assert _is_modifier_variant("Fellow", "FellowOfAcademy")
    assert _is_modifier_variant("Magistrate", "MagistrateInProvince")


def test_modifier_variant_rejects_competing_modifiers():
    # same head, different modifier -> NOT a granularity difference
    assert not _is_modifier_variant("ResidentOfFrance", "ResidentOfGermany")
    assert not _is_modifier_variant("Fellow", "Associate")


def test_modifier_only_divergence_same_structure():
    a = "forall x. (LaureateOfAcademy(x) -> Fellow(x))"
    b = "forall x. (Laureate(x) -> Fellow(x))"
    assert _modifier_only_divergence(a, b)


def test_modifier_only_divergence_false_on_structure_mismatch():
    a = "forall x. (Bank(x) -> Safe(x))"
    b = "exists x. (Bank(x) and Safe(x))"
    assert not _modifier_only_divergence(a, b)


def test_modifier_divergence_resolved_without_judge():
    # The deterministic path must resolve modifier-only divergence with NO judge:
    # both readings pass fidelity, same structure, predicates differ only by a
    # folded modifier -> ACCEPTED (rule reading kept), not AMBIGUOUS.
    v = Vocabulary()
    s = _stmt("Every magistrate in the province is an officer of the charter.")
    prop = run_gate(s, "forall x. (Magistrate(x) -> Officer(x))", v)
    assert prop.status == GateOutcome.ACCEPTED


# --- gate.py judge adjudication of divergence -------------------------------

from consistency_checker.gate import run_gate
from consistency_checker.schema import ExtractedStatement, GateOutcome, StatementType
from consistency_checker.vocabulary import Vocabulary


def _stmt(text):
    return ExtractedStatement(id="g", type=StatementType.AXIOM, original_text=text, decontextualized=text)


def test_modifier_divergence_resolved_by_judge():
    # rule path folds the modifier (MagistrateInProvince); a modifier-dropping
    # LLM reading would otherwise both-pass and go AMBIGUOUS. Judge says the two
    # readings mean the same -> ACCEPTED, not ambiguous.
    v = Vocabulary()
    s = _stmt("Every magistrate in the province is an officer of the charter.")
    prop = run_gate(s, "forall x. (Magistrate(x) -> Officer(x))", v, judge=StubJudge(True))
    assert prop.status == GateOutcome.ACCEPTED


class FnJudge:
    def __init__(self, fn):
        self._fn = fn

    def entails(self, a, b):
        return self._fn(a, b)

    def equivalent(self, a, b):
        return self._fn(a, b)


def test_vocabulary_unifies_coreferent_predicates_with_judge():
    # "fellow" registered first, then "fellow of the academy": judge confirms
    # coreference -> both map to ONE symbol, so a rule and instance phrased
    # differently meet in Z3.
    v = Vocabulary(judge=StubJudge(True))
    bare = v.canonical_pred("Fellow")
    folded = v.pred_from_phrase("fellow of the academy")
    assert bare == folded


def test_vocabulary_keeps_distinct_predicates_when_judge_refuses():
    # resident of France vs resident of Germany: judge says NOT equivalent ->
    # they must stay separate (no manufactured contradiction).
    v = Vocabulary(judge=StubJudge(False))
    fr = v.pred_from_phrase("resident of france")
    de = v.pred_from_phrase("resident of germany")
    assert fr != de


def test_vocabulary_unchanged_without_judge():
    # default path: no judge -> no semantic aliasing, distinct symbols
    v = Vocabulary()
    assert v.canonical_pred("Fellow") != v.pred_from_phrase("fellow of the academy")


# --- deterministic unique-modifier merge (no LLM) ---------------------------

def test_finalize_merges_lone_modifier_onto_bare():
    # bare 'Fellow' + single modified 'FellowOfAcademy' -> one symbol, no judge.
    v = Vocabulary()
    bare = v.canonical_pred("Fellow")
    folded = v.canonical_pred("FellowOfAcademy")
    assert bare != folded  # distinct before finalize
    aliases = v.finalize_modifier_aliases()
    assert aliases.get(folded) == bare
    # registry now reports the single merged symbol
    assert v.canonical_pred("FellowOfAcademy") == bare
    assert bare in v.predicates and folded not in v.predicates


def test_finalize_does_not_merge_competing_modifiers():
    # two modifier variants of 'resident' -> left distinct (no fabricated merge),
    # and a bare 'Resident' is not collapsed into either.
    v = Vocabulary()
    bare = v.canonical_pred("Resident")
    fr = v.canonical_pred("ResidentOfFrance")
    de = v.canonical_pred("ResidentOfGermany")
    aliases = v.finalize_modifier_aliases()
    assert aliases == {}
    assert len({bare, fr, de}) == 3


def test_finalize_rewrites_emitted_fol():
    v = Vocabulary()
    v.canonical_pred("Fellow")
    v.canonical_pred("FellowOfAcademy")
    aliases = v.finalize_modifier_aliases()
    rewritten = v.apply_pred_aliases("forall x. (FellowOfAcademy(x) -> Voter(x))", aliases)
    assert "FellowOfAcademy" not in rewritten and "Fellow(x)" in rewritten


def test_finalize_noop_returns_empty_map():
    v = Vocabulary()
    v.canonical_pred("Fellow")
    v.canonical_pred("Voter")
    assert v.finalize_modifier_aliases() == {}


def test_genuine_ambiguity_preserved_when_judge_says_not_equivalent():
    # A discerning judge: each reading IS faithful to the source (any pair that
    # includes the source sentence -> True), but the two readings are NOT
    # equivalent to each other (verbalization-vs-verbalization -> False). So both
    # pass fidelity, adjudication says not-equivalent -> stays AMBIGUOUS.
    src = "Every bank is safe."
    judge = FnJudge(lambda a, b: src in (a, b))
    v = Vocabulary()
    prop = run_gate(_stmt(src), "exists x. (Bank(x) and Safe(x))", v, judge=judge)
    assert prop.status == GateOutcome.AMBIGUOUS


def test_gate_default_path_unchanged_without_judge():
    # the original genuine-ambiguity case, no judge -> still AMBIGUOUS
    v = Vocabulary()
    s = _stmt("Every bank is safe.")
    prop = run_gate(s, "exists x. (Bank(x) and Safe(x))", v)
    assert prop.status == GateOutcome.AMBIGUOUS
