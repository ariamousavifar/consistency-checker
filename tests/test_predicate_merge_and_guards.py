"""Tests for the two deterministic false-negative fixes (post-v0.8.7):

1. Curated relational-synonym merge (vocabulary.py): Require/Prerequisite named
   off different surface forms of ONE relation, with the argument-direction flip
   ('X requires Y' == 'Y is a prerequisite for X'). Fixes rel_prereq_broken.
2. Guarded-irreflexivity normalization (normalize.py): a type guard nothing in
   the document instantiates is stripped so the axiom is not vacuously true.
   Fixes rel_genealogy_broken.
3. N8 statement-level translation cache (extraction.py): resumed runs never
   redo completed translations; nulls are re-attempted.

The integration tests use the EXACT FOL shapes stored by the failing live runs
(results/out1 + results/out2 store.json), so a pass here means those two false
negatives are fixed at the logic level.
"""
from __future__ import annotations

import json

import pytest

from src.extraction import LiveTranslator
from src.normalize import strip_dangling_guards
from src.schema import ExtractedStatement, GateOutcome, Proposition, StatementType
from src.solver import verify
from src.vocabulary import Vocabulary, predicate_arities


def _prop(pid, fol, ptype=StatementType.AXIOM, status=GateOutcome.ACCEPTED):
    return Proposition(
        id=pid, type=ptype, original_text=pid, decontextualized=pid,
        fol=fol, status=status,
    )


def _register_all(vocab: Vocabulary, props):
    for p in props:
        if p.fol:
            p.fol = vocab.normalize_fol(p.fol)


def _run_relation_merge(vocab: Vocabulary, props):
    aliases, provenance = vocab.finalize_relation_synonym_aliases(
        [p.fol for p in props if p.fol])
    for p in props:
        if p.fol:
            p.fol = vocab.apply_relation_synonym_aliases(p.fol, aliases)
    return aliases, provenance


# --------------------------------------------------------------------------
# 1. relational-synonym merge
# --------------------------------------------------------------------------

class TestRelationSynonymMerge:
    def test_prereq_merge_is_directional(self):
        """Prerequisite(a, b) ('a is a prerequisite for b') must become
        Require(b, a) ('b requires a') -- renamed AND argument-swapped."""
        vocab = Vocabulary()
        props = [
            _prop("s1", "Require(c6100b, c6100a)"),
            _prop("s2", "Require(c61010, c6100a)"),
            _prop("s3", "forall a. (forall b. (forall c. ((Prerequisite(a, b) "
                        "and Prerequisite(b, c)) -> Prerequisite(a, c))))"),
        ]
        _register_all(vocab, props)
        aliases, provenance = _run_relation_merge(vocab, props)

        assert "Prerequisite" in aliases
        canon, swapped = aliases["Prerequisite"]
        assert canon == "Require"    # dominant form: 2 statements use Require, 1 uses Prerequisite
        assert swapped is True       # inverse phrasing -> args flip
        # the rewritten rule is transitivity of Require with flipped args
        rule = props[2].fol
        assert "Prerequisite" not in rule
        assert "Require(b, a)" in rule and "Require(c, b)" in rule
        assert "-> Require(c, a)" in rule
        assert provenance and provenance[0]["args_swapped"] is True

    def test_prereq_cycle_closes_after_merge(self):
        """The rel_prereq_broken false negative, reproduced from the live run's
        stored FOL: Require-edges + Prerequisite-rules. After the merge the
        planted cycle must be found INCONSISTENT."""
        vocab = Vocabulary()
        props = [
            _prop("s2", "Require(c6100b, c6100a)"),
            _prop("s5", "Require(c61020, c61010)"),
            _prop("s3", "Require(c61010, c6100a)"),
            _prop("s8", "Require(c61060, c61020)"),
            _prop("s11", "Require(c65060, c61060)"),
            _prop("s15", "Require(c6100a, c65060)"),   # the planted edge
            _prop("s13", "forall a. (forall b. (forall c. ((Prerequisite(a, b) "
                         "and Prerequisite(b, c)) -> Prerequisite(a, c))))"),
            _prop("s14", "forall x. (not Prerequisite(x, x))"),
        ]
        _register_all(vocab, props)
        _run_relation_merge(vocab, props)
        reports = verify(props, timeout_ms=8000)
        assert any(r.axioms_consistent is False for r in reports), \
            "prerequisite cycle must be detected after Require/Prerequisite merge"

    def test_consistent_chain_stays_clean_after_merge(self):
        """FP control: the same chain WITHOUT the planted edge must stay
        consistent after the merge (the merge cannot invent a contradiction)."""
        vocab = Vocabulary()
        props = [
            _prop("s2", "Require(c6100b, c6100a)"),
            _prop("s5", "Require(c61020, c61010)"),
            _prop("s3", "Require(c61010, c6100a)"),
            _prop("s11", "Require(c65060, c61060)"),
            _prop("s13", "forall a. (forall b. (forall c. ((Prerequisite(a, b) "
                         "and Prerequisite(b, c)) -> Prerequisite(a, c))))"),
            _prop("s14", "forall x. (not Prerequisite(x, x))"),
        ]
        _register_all(vocab, props)
        _run_relation_merge(vocab, props)
        reports = verify(props, timeout_ms=8000)
        assert all(r.axioms_consistent is not False for r in reports)

    def test_unrelated_relations_never_merge(self):
        """Negative: Loves/Hates (not in the curated table) must not merge --
        no embeddings, no fuzzy matching, antonyms structurally impossible."""
        vocab = Vocabulary()
        props = [
            _prop("s1", "Loves(a, b)"),
            _prop("s2", "Hates(a, b)"),
            _prop("s3", "Governs(a, b)"),
            _prop("s4", "RulesOver(a, b)"),   # govern/rule deliberately NOT curated
        ]
        _register_all(vocab, props)
        aliases, _ = _run_relation_merge(vocab, props)
        assert aliases == {}

    def test_arity_mismatch_blocks_merge(self):
        """Negative: a curated word used at inconsistent arity never merges."""
        vocab = Vocabulary()
        props = [
            _prop("s1", "Require(c6100b, c6100a)"),
            _prop("s2", "Prerequisite(c6100a)"),   # unary usage -> not a relation
        ]
        _register_all(vocab, props)
        aliases, _ = _run_relation_merge(vocab, props)
        assert aliases == {}

    def test_single_member_group_never_fires(self):
        vocab = Vocabulary()
        props = [_prop("s1", "Require(a, b)"), _prop("s2", "Owns(a, b)")]
        _register_all(vocab, props)
        aliases, _ = _run_relation_merge(vocab, props)
        assert aliases == {}

    def test_multiword_names_never_match(self):
        """PushToRaiseTax contains 'to' (particle) + multiple content words; it
        must never reduce to a curated word."""
        vocab = Vocabulary()
        props = [
            _prop("s1", "PushToRaiseTax(congress, author)"),
            _prop("s2", "Require(a, b)"),
        ]
        _register_all(vocab, props)
        aliases, _ = _run_relation_merge(vocab, props)
        assert aliases == {}

    def test_containment_group_with_flip(self):
        """Contains(x, y) is the inverse phrasing of LocatedIn(y, x)."""
        vocab = Vocabulary()
        props = [
            _prop("s1", "LocatedIn(munich, bavaria)"),
            _prop("s2", "LocatedIn(bavaria, germany)"),
            _prop("s3", "Contains(germany, bavaria)"),
        ]
        _register_all(vocab, props)
        aliases, _ = _run_relation_merge(vocab, props)
        # registry display name plural-strips Contains -> Contain
        assert aliases.get("Contain") == ("LocatedIn", True)
        assert props[2].fol == "LocatedIn(bavaria, germany)"

    def test_arities_helper(self):
        arities = predicate_arities([
            "Require(a, b)", "forall x. (not Require(x, x))", "Human(socrates)",
        ])
        assert arities["Require"] == {2}
        assert arities["Human"] == {1}


# --------------------------------------------------------------------------
# 2. guarded-irreflexivity normalization
# --------------------------------------------------------------------------

class TestDanglingGuardStrip:
    def _genealogy(self, with_person_fact=False):
        props = [
            _prop(f"s{i}", f"FatherOf({a}, {b})", ptype=StatementType.DERIVED_CLAIM)
            for i, (a, b) in enumerate([
                ("adam", "seth"), ("seth", "enosh"), ("enosh", "kenan"),
                ("kenan", "mahalalel"), ("mahalalel", "jared"), ("jared", "enoch"),
                ("enoch", "methuselah"), ("methuselah", "lamech"), ("lamech", "noah"),
                ("noah", "shem"), ("shem", "arphaxad"), ("arphaxad", "shelah"),
                ("shelah", "eber"), ("eber", "peleg"),
            ], start=1)
        ]
        props += [
            _prop("s15", "forall x. (forall y. (FatherOf(x, y) -> Ancestor(x, y)))"),
            _prop("s16", "forall x. (forall y. (forall z. ((Ancestor(x, y) "
                         "and Ancestor(y, z)) -> Ancestor(x, z))))"),
            # the live run's over-guarded irreflexivity (the false negative)
            _prop("s17", "forall x. (Person(x) -> not Ancestor(x, x))"),
            _prop("s18", "Ancestor(peleg, adam)"),
        ]
        if with_person_fact:
            props.append(_prop("s19", "Person(adam)"))
        return props

    def test_genealogy_guard_stripped_and_cycle_found(self):
        """The rel_genealogy_broken false negative: Person() is uninstantiated,
        so the guard must be stripped and the 14-hop ancestry cycle refuted."""
        props = self._genealogy()
        provenance = strip_dangling_guards(props)
        assert len(provenance) == 1
        assert provenance[0]["guard"] == "Person"
        s17 = next(p for p in props if p.id == "s17")
        assert s17.fol == "forall x. (not Ancestor(x, x))"
        assert "dangling type-guard" in s17.gate_reason
        reports = verify(props, timeout_ms=15000)
        assert any(r.axioms_consistent is False for r in reports), \
            "ancestry cycle must be detected once the vacuous guard is gone"

    def test_guard_kept_when_type_is_instantiated(self):
        """A populated type is a REAL guard: Person(adam) asserted -> no strip."""
        props = self._genealogy(with_person_fact=True)
        provenance = strip_dangling_guards(props)
        assert provenance == []
        s17 = next(p for p in props if p.id == "s17")
        assert s17.fol == "forall x. (Person(x) -> not Ancestor(x, x))"

    def test_positive_consequent_never_stripped(self):
        """'All unicorns are immortal' must NOT become 'everything is immortal'
        even though Unicorn is uninstantiated -- stripping a positive-consequent
        rule would MANUFACTURE contradictions (the unicorn counterexample)."""
        props = [
            _prop("s1", "forall x. (Unicorn(x) -> Immortal(x))"),
            _prop("s2", "not Immortal(socrates)"),
        ]
        provenance = strip_dangling_guards(props)
        assert provenance == []
        assert props[0].fol == "forall x. (Unicorn(x) -> Immortal(x))"
        reports = verify(props, timeout_ms=8000)
        assert all(r.axioms_consistent is not False for r in reports)

    def test_negated_unary_consequent_never_stripped(self):
        """Only the REFLEXIVE-relational shape (not R(x, x)) qualifies; a negated
        unary consequent ('no person is immortal') stays guarded."""
        props = [_prop("s1", "forall x. (Person(x) -> not Immortal(x))")]
        assert strip_dangling_guards(props) == []
        assert props[0].fol == "forall x. (Person(x) -> not Immortal(x))"

    def test_quarantined_statements_ignored(self):
        props = [
            _prop("s1", "forall x. (Person(x) -> not Ancestor(x, x))",
                  status=GateOutcome.QUARANTINED),
        ]
        assert strip_dangling_guards(props) == []


# --------------------------------------------------------------------------
# 3. N8 translation cache (statement-level resume)
# --------------------------------------------------------------------------

class TestConstantSpellingUnification:
    """The N15 family completed: number-word and letter-tag spellings of one
    code must canonicalize to the same VALID constant. Root cause found by
    controlled experiment -- the model reuses an existing 'six_5060' from the
    vocabulary while another statement coined 'c65060', splitting 6.5060's node
    so the prereq cycle never closes."""

    def test_all_spellings_of_one_code_unify(self):
        from src.vocabulary import _const_key
        forms = ["c65060", "c6_5060", "six_5060", "six5060"]
        keys = {_const_key(f) for f in forms}
        assert len(keys) == 1, keys
        assert keys == {"c65060"}   # a valid, parseable identifier

    def test_distinct_codes_stay_distinct(self):
        from src.vocabulary import _const_key
        assert _const_key("six_5060") != _const_key("six_1060")   # 6.5060 vs 6.1060
        assert _const_key("c6100a") != _const_key("c65060")

    def test_non_code_names_untouched(self):
        from src.vocabulary import _const_key
        for n in ("socrates", "old_ferry", "two", "the_blue", "date_2000"):
            assert _const_key(n) == n

    def test_canonical_forms_are_parseable(self):
        from src.vocabulary import _const_key
        from src.fol_parser import parse_fol, Env
        for f in ("six_5060", "six5060", "c6_5060", "65060x"):
            parse_fol(f"P({_const_key(f)})", Env())   # must not raise

    def test_split_spelling_cycle_closes(self):
        """End-to-end: a prereq cycle whose 6.5060 node is written 'six_5060' in
        one edge and 'c65060' in another must still be found INCONSISTENT after
        constant canonicalization."""
        vocab = Vocabulary()
        props = [
            _prop("s11", "Require(six_5060, c61060)"),   # 6.5060 as six_5060
            _prop("s12", "Require(c65060, c61220)"),     # 6.5060 as c65060
            _prop("s15", "Require(c6100a, six_5060)"),   # planted edge -> 6.5060
            _prop("s7", "Require(c61060, c6100a)"),      # 6.1060 requires 6.100A (closes loop)
            _prop("s14", "forall x. (not Require(x, x))"),
            _prop("s13", "forall x. (forall y. (forall z. ((Require(x, y) "
                         "and Require(y, z)) -> Require(x, z))))"),
        ]
        _register_all(vocab, props)
        # after normalize_fol, six_5060 and c65060 must be the same token
        fols = " ".join(p.fol for p in props)
        assert "six" not in fols.lower()             # unified away
        reports = verify(props, timeout_ms=8000)
        assert any(r.axioms_consistent is False for r in reports)


class TestFidelityNumberWordCodes:
    def test_spelled_out_digit_matches_code(self):
        """Live failure (results/out_fix_prereq2 s11): the translator coined
        '6.5060' as six_5060, and the signature match failed -> a correct
        translation was quarantined and the cycle edge dropped. The number-word
        form must match."""
        from src.fidelity import fidelity_check
        res = fidelity_check("Require(six_5060, six_1060)", "6.5060 requires 6.1060.")
        assert res.passed, f"coverage={res.coverage} missing={res.missing}"

    def test_letter_tag_form_still_matches(self):
        from src.fidelity import fidelity_check
        res = fidelity_check("Require(c65060, c61060)", "6.5060 requires 6.1060.")
        assert res.passed


class _StubClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = 0
        self.asked: list[str] = []

    def complete_json(self, system, user, retries=2, reasoning_effort=None):
        self.calls += 1
        payload = json.loads(user)
        ids = [s["id"] for s in payload["statements"]]
        self.asked.extend(ids)
        return {sid: self.mapping.get(sid) for sid in ids}


def _stmts():
    return [
        ExtractedStatement(id="s1", type=StatementType.AXIOM,
                           original_text="a", decontextualized="All men are mortal."),
        ExtractedStatement(id="s2", type=StatementType.AXIOM,
                           original_text="b", decontextualized="Socrates is a man."),
        ExtractedStatement(id="s3", type=StatementType.AXIOM,
                           original_text="c", decontextualized="Hard modal sentence."),
    ]


class TestTranslationCache:
    def test_resume_skips_completed_and_retries_nulls(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_TRANSLATION_RETRY", "0")
        cache = tmp_path / "translation.partial.jsonl"

        # run 1: s1/s2 translate, s3 fails (null)
        stub1 = _StubClient({"s1": "forall x. (Man(x) -> Mortal(x))",
                             "s2": "Man(socrates)", "s3": None})
        t1 = LiveTranslator(stub1)
        out1 = t1.translate(_stmts(), Vocabulary(), cache_path=cache)
        assert out1["s1"] and out1["s2"] and out1["s3"] is None
        assert cache.exists()
        lines = [json.loads(l) for l in cache.read_text().splitlines()]
        assert len(lines) == 2                      # nulls are NOT checkpointed

        # run 2 (the provider-swap/crash-recovery rerun): s1/s2 come from the
        # cache without any API call; only s3 is re-asked.
        stub2 = _StubClient({"s3": "HardModal(x1)"})
        t2 = LiveTranslator(stub2)
        out2 = t2.translate(_stmts(), Vocabulary(), cache_path=cache)
        assert out2["s1"] == out1["s1"] and out2["s2"] == out1["s2"]
        assert out2["s3"] == "HardModal(x1)"
        assert stub2.asked == ["s3"]                # completed work never redone

    def test_stale_cache_never_matches_changed_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_TRANSLATION_RETRY", "0")
        cache = tmp_path / "translation.partial.jsonl"
        stub1 = _StubClient({"s1": "P(a)", "s2": "Q(b)", "s3": "R(c)"})
        LiveTranslator(stub1).translate(_stmts(), Vocabulary(), cache_path=cache)

        changed = _stmts()
        changed[0].decontextualized = "A COMPLETELY DIFFERENT SENTENCE."
        stub2 = _StubClient({"s1": "Different(d)"})
        out = LiveTranslator(stub2).translate(changed, Vocabulary(), cache_path=cache)
        assert out["s1"] == "Different(d)"          # content hash rejects stale entry
        assert stub2.asked == ["s1"]

    def test_cache_disabled_by_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_TRANSLATION_RETRY", "0")
        monkeypatch.setenv("LLM_TRANSLATION_CACHE", "0")
        cache = tmp_path / "translation.partial.jsonl"
        stub = _StubClient({"s1": "P(a)", "s2": "Q(b)", "s3": "R(c)"})
        LiveTranslator(stub).translate(_stmts(), Vocabulary(), cache_path=cache)
        assert not cache.exists()

    def test_torn_last_line_tolerated(self, tmp_path, monkeypatch):
        """A crash mid-write leaves a torn JSON line; resume must skip it."""
        monkeypatch.setenv("LLM_TRANSLATION_RETRY", "0")
        cache = tmp_path / "translation.partial.jsonl"
        good = json.dumps({"key": None, "fol": None})  # placeholder; build real key
        s = _stmts()[0]
        key = LiveTranslator._cache_key(s)
        cache.write_text(json.dumps({"key": key, "fol": "P(a)"}) + "\n"
                         + '{"key": "s2|deadbeef", "fol": "Q(', encoding="utf-8")
        stub = _StubClient({"s2": "Q(b)", "s3": "R(c)"})
        out = LiveTranslator(stub).translate(_stmts(), Vocabulary(), cache_path=cache)
        assert out["s1"] == "P(a)"
        assert sorted(stub.asked) == ["s2", "s3"]
