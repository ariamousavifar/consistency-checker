"""End-to-end: the modifier-inconsistent chain (the *original* t25c shape) must
be detected through the real gate + vocabulary + solver by the DETERMINISTIC
unique-modifier merge -- no LLM judge required -- and must NOT be detected if
that merge is skipped. This is the regression proof that the fix lives in the
pipeline (reproducibly, for free), not in simplified fixture text.

The chain, phrased the hard way (modifier used inconsistently across sentences):
    Every laureate is a fellow.                    (bare 'fellow')
    Every fellow of the Academy is an associate.   ('fellow of the Academy')
    Every associate is a voter.
    Aldous is a laureate.
    Aldous is not a voter.       -> contradiction iff Fellow == FellowOfAcademy
"""
from consistency_checker.gate import run_gate
from consistency_checker.schema import ExtractedStatement, StatementType
from consistency_checker.solver import verify
from consistency_checker.vocabulary import Vocabulary


_CHAIN = [
    ("s1", "Every laureate is a fellow."),
    ("s2", "Every fellow of the Academy is an associate."),
    ("s3", "Every associate is a voter."),
    ("s4", "Aldous is a laureate."),
    ("s5", "Aldous is not a voter."),
]


def _run(vocab, unify):
    """Mirror the pipeline: gate every statement, then (optionally) apply the
    deterministic predicate merge and rewrite the emitted FOL, then solve."""
    props = []
    for sid, text in _CHAIN:
        stmt = ExtractedStatement(
            id=sid, type=StatementType.AXIOM, original_text=text, decontextualized=text
        )
        props.append(run_gate(stmt, None, vocab))  # rule-only translation
    if unify:
        aliases = vocab.finalize_modifier_aliases()
        for p in props:
            if p.fol:
                p.fol = vocab.apply_pred_aliases(p.fol, aliases)
    return verify(props, effort=1)


def test_modifier_inconsistent_chain_detected_by_deterministic_merge():
    clusters = _run(Vocabulary(), unify=True)
    assert any(c.axioms_consistent is False for c in clusters), \
        "expected an inconsistent set once Fellow/FellowOfAcademy are merged (no judge)"


def test_modifier_inconsistent_chain_missed_without_merge():
    # The honest control: skip the merge and the modifier breaks the chain, so
    # the contradiction is invisible -- exactly the failure the merge fixes.
    clusters = _run(Vocabulary(), unify=False)
    assert all(c.axioms_consistent is not False for c in clusters), \
        "without the merge the symbols should not connect, so no contradiction"
