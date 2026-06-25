"""Fidelity must not penalize VOCABULARY REUSE as if it were invention.

A predicate coined and grounded by an earlier statement, then reused in a later
'therefore' conclusion whose sentence phrases the concept differently, used to
fail the lexical-coverage gate and quarantine a correct conclusion. The
`known_preds` exemption fixes that while STILL catching a freshly-invented
predicate the sentence never mentions (a real mistranslation).
"""
from src.fidelity import fidelity_check


# The real Rothbard cases that motivated the fix.
_S24 = "Therefore, any society without full self-ownership for everyone cannot have a universal ethic"
_F24 = "forall x. (not RightToFullBodyOwnership(x) -> not ProvideUniversalEthic(x))"
_S25 = "Thus, 100 percent self-ownership for every man is the only viable political ethic for mankind"
_F25 = "forall x. (Man(x) -> EntitledToOwnSelfAndRemainder(x))"


def test_reused_predicate_words_are_exempt():
    # 'RightToFullBodyOwnership' was coined earlier; 'body'/'right' need not recur.
    base = fidelity_check(_F24, _S24)
    assert not base.passed  # the old behavior: reuse looked like invention
    fixed = fidelity_check(_F24, _S24, known_preds={"RightToFullBodyOwnership"})
    assert fixed.passed
    assert "body" not in fixed.missing and "right" not in fixed.missing


def test_fresh_invented_predicate_still_fails():
    # 'EntitledToOwnSelfAndRemainder' is coined HERE and is a genuine
    # mistranslation (conflates the conclusion with alternative-2); it must
    # still fail even though 'Man' is a known/reused symbol.
    fixed = fidelity_check(_F25, _S25, known_preds={"Man"})
    assert not fixed.passed
    assert "entitled" in fixed.missing and "remainder" in fixed.missing


def test_no_known_preds_is_unchanged_behavior():
    # Default call (no known_preds) must be byte-identical to before the change.
    assert fidelity_check(_F24, _S24).coverage == fidelity_check(_F24, _S24, known_preds=set()).coverage
