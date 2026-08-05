"""Deterministic generic-hedge guard + quarantine-shape classifier."""
from consistency_checker.gate import run_gate
from consistency_checker.linguistics import deontic_cue, hedge_cue, quarantine_shape
from consistency_checker.schema import ExtractedStatement, GateOutcome, StatementType
from consistency_checker.vocabulary import Vocabulary


# --- hedge detection --------------------------------------------------------

def test_hedge_detects_frequency_adverb():
    assert hedge_cue("Birds typically fly.") == "typically"
    assert hedge_cue("Magistrates usually publish their decisions.") == "usually"


def test_hedge_detects_phrase():
    assert hedge_cue("Ceteris paribus, demand falls when price rises.") == "ceteris paribus"
    assert hedge_cue("As a rule, every elder has served a term.") == "as a rule"


def test_hedge_none_on_strict_universal():
    assert hedge_cue("Every magistrate is an officer.") is None
    assert hedge_cue("Socrates is mortal.") is None


def test_hedge_excludes_counting_words():
    # 'most'/'many' are precise quantifiers, not defeasibility markers -> not a hedge
    assert hedge_cue("Most citizens are merchants.") is None


def test_hedge_checks_all_texts():
    # decontextualization stripped the 'typically'; original still carries it
    assert hedge_cue("Birds fly.", "Birds typically fly.") == "typically"


def test_hedge_word_boundary_no_false_match():
    # 'usual hours' must NOT match the hedge word 'usually'
    assert hedge_cue("Access is granted during the usual hours.") is None


# --- deontic detection ------------------------------------------------------

def test_deontic_detects_modal_word():
    assert deontic_cue("Every magistrate must publish their decisions.") == "must"
    assert deontic_cue("A citizen should obey the law.") == "should"


def test_deontic_detects_phrase():
    assert deontic_cue("Each man is entitled to full self-ownership.") == "entitled to"
    assert deontic_cue("He has a duty to act.") == "duty to"


def test_deontic_none_on_descriptive():
    assert deontic_cue("Socrates is mortal.") is None
    assert deontic_cue("Every magistrate is an officer.") is None


def test_deontic_word_boundary_no_false_match():
    # 'mustard'/'shoulder' must NOT match 'must'/'should'
    assert deontic_cue("The mustard is on the shoulder of the road.") is None


# --- self-reference constant unification ------------------------------------

def test_self_ref_merges_speaker_onto_author():
    v = Vocabulary()
    a = v.normalize_fol("not RaiseTax(speaker)")
    b = v.normalize_fol("RaiseTax(author)")
    aliases = v.finalize_self_reference_aliases()
    assert aliases == {"speaker": "author"}
    assert v.apply_const_aliases(a, aliases) == "not RaiseTax(author)"
    assert v.apply_const_aliases(b, aliases) == "RaiseTax(author)"


def test_self_ref_noop_with_single_label():
    v = Vocabulary()
    v.normalize_fol("RaiseTax(speaker)")
    assert v.finalize_self_reference_aliases() == {}


def test_self_ref_leaves_bound_variables_untouched():
    v = Vocabulary()
    v.normalize_fol("not RaiseTax(speaker)")
    v.normalize_fol("RaiseTax(author)")
    aliases = v.finalize_self_reference_aliases()
    assert v.apply_const_aliases("forall x. (Human(x) -> Mortal(x))", aliases) == \
        "forall x. (Human(x) -> Mortal(x))"


# --- shape classification ---------------------------------------------------

def test_shape_modal_deontic():
    assert quarantine_shape("Every magistrate must publish their decisions.") == "modal-deontic"


def test_shape_comparative_numeric():
    assert quarantine_shape("More than half of the citizens are merchants.") == "comparative-numeric"


def test_shape_causal():
    assert quarantine_shape("Intervention causes malinvestment.") == "causal"


def test_shape_transitive_ordering():
    assert quarantine_shape("The laureate grade ranks above the fellow grade.") == "transitive-ordering"


def test_shape_relational_role_universal():
    assert quarantine_shape("Every city is located in some country.") == "relational-role(∀∃)"


def test_shape_relational_ground():
    assert quarantine_shape("Paris is located in France.") == "relational-ground"


def test_shape_other_for_plain_unary():
    assert quarantine_shape("The charter is old.") == "other"


# --- gate integration -------------------------------------------------------

def _stmt(text):
    return ExtractedStatement(id="g", type=StatementType.AXIOM, original_text=text, decontextualized=text)


def test_gate_quarantines_hedged_statement():
    v = Vocabulary()
    prop = run_gate(_stmt("Every bird typically flies."), None, v)
    assert prop.status == GateOutcome.QUARANTINED
    assert "defeasible" in prop.gate_reason and "typically" in prop.gate_reason
    # a hedge exclusion is not a fragment-limit, so no shape is attached
    assert prop.quarantine_shape is None


def test_gate_attaches_shape_to_outside_fragment_quarantine():
    v = Vocabulary()
    # a relational statement the rule fragment can't take, no LLM candidate
    prop = run_gate(_stmt("Alice owns the dangerous book."), None, v)
    assert prop.status == GateOutcome.QUARANTINED
    assert prop.quarantine_shape is not None


def test_gate_does_not_over_quarantine_strict_universal():
    v = Vocabulary()
    prop = run_gate(_stmt("Every magistrate is an officer."), None, v)
    assert prop.status == GateOutcome.ACCEPTED


def test_gate_deontic_guard_off_by_default():
    # without the opt-in flag, a prescriptive universal is NOT quarantined for it
    v = Vocabulary()
    prop = run_gate(_stmt("Every magistrate must be an officer."), None, v)
    assert "deontic" not in (prop.gate_reason or "")


def test_gate_deontic_guard_quarantines_when_enabled():
    v = Vocabulary()
    prop = run_gate(_stmt("Every magistrate must be an officer."), None, v, guard_deontic=True)
    assert prop.status == GateOutcome.QUARANTINED
    assert "deontic" in prop.gate_reason and "must" in prop.gate_reason
    # a policy exclusion, not a fragment limit -> no shape attached
    assert prop.quarantine_shape is None


def test_gate_deontic_guard_leaves_descriptive_alone():
    v = Vocabulary()
    prop = run_gate(_stmt("Every magistrate is an officer."), None, v, guard_deontic=True)
    assert prop.status == GateOutcome.ACCEPTED
