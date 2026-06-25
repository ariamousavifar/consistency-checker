"""Fidelity must treat alphanumeric CODE constants (course numbers, identifiers)
as content, not invention.

A relational ground fact like 'Require(c6100b, c6100a)' for the sentence
'6.100B requires 6.100A' used to fail the lexical-coverage gate: the course-code
constants ('c6100b') don't lemma-match the sentence tokens, and the pure-digit
pieces ('5060') were dropped as digits -- coverage 0.333 -> quarantine, which
silently removed every prerequisite edge so a planted cycle could never close.

The fix matches a code-like constant by its alphanumeric signature against the
sentence's punctuation-stripped text ('6.100B' -> '...6100b...'), while a code
that is genuinely absent from the sentence still fails.
"""
from src.fidelity import _code_forms, _is_code, fidelity_check


def test_code_constants_no_underscore_pass():
    r = fidelity_check("Require(c6100b, c6100a)", "6.100B requires 6.100A.")
    assert r.passed and r.coverage == 1.0


def test_code_constants_underscore_pass():
    # the underscore form whose digit pieces were dropped as 'isdigit' before
    r = fidelity_check("Require(c6_5060, c6_1060)",
                       "6.5060 (Algorithm Engineering) requires 6.1060.")
    assert r.passed and r.coverage == 1.0


def test_invented_code_still_fails():
    r = fidelity_check("Require(c9999z, c8888y)", "6.100B requires 6.100A.")
    assert not r.passed
    assert "c9999z" in r.missing and "c8888y" in r.missing


def test_is_code_discriminates():
    assert _is_code("c6100b") and _is_code("c6_5060") and _is_code("6100a")
    # words and word-like constants are not codes (so the word path still runs)
    assert not _is_code("socrates") and not _is_code("old_ferry")
    assert not _is_code("date") and not _is_code("course")  # date/course: too many leading letters


def test_code_forms_detags_leading_letter():
    assert "6100b" in _code_forms("c6100b")     # de-tagged digits
    assert "65060" in _code_forms("c6_5060")    # separators stripped, tag peeled


def test_word_constant_path_unaffected():
    # a non-code multi-word constant still scores per-word against the sentence
    r = fidelity_check("not HasPopulation(old_ferry)", "Old Ferry has no population.")
    assert r.passed
