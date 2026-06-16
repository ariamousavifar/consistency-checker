"""v0.5 regression tests: the two false-negative bugs found in live Tier-1/2
runs. Both were silent (the tool said 'all clear' when it should have flagged a
contradiction), so these are the most important guards in the suite."""
from __future__ import annotations

from src.fidelity import fidelity_check
from src.schema import GateOutcome, Proposition, StatementType, Verdict
from src.solver import verify
from src.vocabulary import Vocabulary, pred_key


def _acc(id, typ, fol, vocab):
    return Proposition(id=id, type=typ, original_text=id, decontextualized=id,
                       fol=vocab.normalize_fol(fol), status=GateOutcome.ACCEPTED, confidence=0.9)


# ---------- Bug 1: fidelity over-quarantining (false negatives) ----------

def test_fidelity_accepts_multiword_constant():
    # "the blue" -> theblue tokenization must not fail an otherwise perfect match
    assert fidelity_check("Whale(theblue)", "The blue is a whale.").passed
    assert fidelity_check("Belief(herconviction)", "Her conviction is a belief.").passed
    assert fidelity_check("RiverCity(old_ferry)", "Old Ferry is a river city.").passed


def test_fidelity_ignores_auxiliary_words():
    # the missing word was 'has' / 'have' which carries no logical content
    assert fidelity_check("not Access(devon)", "And yet Devon does not have access").passed
    assert fidelity_check("HasAccess(devon)", "Devon has access").passed


def test_fidelity_still_rejects_invented_content():
    # the fix must not make the gate useless
    assert not fidelity_check("Immortal(zeus)", "Socrates is mortal.").passed
    assert not fidelity_check("Purple(sky) and Loud(thunder)", "The cat is small.").passed


def test_t6_threehop_no_longer_silently_quarantined():
    """The exact Llama failure: 'Devon does not have access' was quarantined for
    a missing auxiliary, so the 3-hop contradiction vanished. It must now be
    caught."""
    v = Vocabulary()
    props = [
        _acc("s1", StatementType.AXIOM, "forall x. (Member(x) -> Subscriber(x))", v),
        _acc("s2", StatementType.AXIOM, "forall x. (Subscriber(x) -> User(x))", v),
        _acc("s3", StatementType.AXIOM, "forall x. (User(x) -> HasAccess(x))", v),
        _acc("s4", StatementType.AXIOM, "Member(devon)", v),
        _acc("s5", StatementType.DERIVED_CLAIM, "not HasAccess(devon)", v),
    ]
    verify(props)
    by_id = {p.id: p for p in props}
    assert by_id["s5"].verdict == Verdict.CONTRADICTS


# ---------- Bug 2: multi-word predicate canonicalization (ambiguous-collapse) ----------

def test_prime_number_and_prime_merge():
    assert pred_key("PrimeNumber") == pred_key("Prime")
    assert pred_key("NaturalNumber") == pred_key("Natural")


def test_light_head_noun_alone_is_preserved():
    v = Vocabulary()
    assert v.canonical_pred("Number") == "Number"
    assert v.canonical_pred("Creature") == "Creature"


def test_t2f_definitional_contradiction_detected():
    """Two translators previously disagreed (Prime vs PrimeNumber), collapsing
    everything to ambiguous and missing the contradiction. With light-head
    merging they align and the contradiction surfaces."""
    v = Vocabulary()
    props = [
        _acc("s1", StatementType.AXIOM, "forall x. (PrimeNumber(x) -> NaturalNumber(x))", v),
        _acc("s3", StatementType.AXIOM, "forall x. (Natural(x) -> Integer(x))", v),
        _acc("s4", StatementType.AXIOM, "Prime(two)", v),
        _acc("s5", StatementType.AXIOM, "Integer(two)", v),
        _acc("s6", StatementType.AXIOM, "forall x. (Prime(x) -> not Integer(x))", v),
    ]
    verify(props)
    by_id = {p.id: p for p in props}
    # the minimal contradiction is {Prime(two), Integer(two), no prime is integer}
    assert by_id["s4"].verdict == Verdict.CONTRADICTS
    assert by_id["s6"].verdict == Verdict.CONTRADICTS
    assert "Prime" not in [pred for p in props for pred in [p.fol]]  # canonicalized away


def test_taxonomy_consistent_has_no_false_positive():
    """t2a must stay clean: the fidelity fix must not introduce false positives."""
    v = Vocabulary()
    props = [
        _acc("s1", StatementType.AXIOM, "forall x. (DomesticCat(x) -> Feline(x))", v),
        _acc("s2", StatementType.AXIOM, "forall x. (Feline(x) -> Carnivore(x))", v),
        _acc("s3", StatementType.AXIOM, "forall x. (Carnivore(x) -> Animal(x))", v),
        _acc("s4", StatementType.AXIOM, "forall x. (Animal(x) -> not Plant(x))", v),
        _acc("s5", StatementType.AXIOM, "DomesticCat(tabby)", v),
        _acc("s6", StatementType.DERIVED_CLAIM, "Carnivore(tabby)", v),
        _acc("s7", StatementType.DERIVED_CLAIM, "not Plant(tabby)", v),
    ]
    verify(props)
    by_id = {p.id: p for p in props}
    assert by_id["s6"].verdict == Verdict.ENTAILED
    assert by_id["s7"].verdict == Verdict.ENTAILED
    assert all(p.verdict != Verdict.CONTRADICTS for p in props)
