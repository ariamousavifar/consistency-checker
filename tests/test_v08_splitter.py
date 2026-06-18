"""v0.8: appositive / participial / relative-clause splitting.

The atomic copular fact must survive when the extractor leaves it glued to a
trailing adjunct that is itself outside the FOL fragment.
"""
from src.splitter import split_statement


def test_participial_comma_adjunct_peels_off_fact():
    parts = split_statement(
        "Aldous is a laureate, having been elevated to that highest grade at a convocation."
    )
    assert parts[0] == "Aldous is a laureate."
    assert parts[1].startswith("having been elevated")


def test_bare_relative_clause_carries_subject():
    parts = split_statement("Aldous is a laureate who was elevated to the highest grade.")
    assert parts[0] == "Aldous is a laureate."
    assert parts[1] == "Aldous was elevated to the highest grade."


def test_does_not_split_plain_copular_fact():
    assert split_statement("Aldous is a laureate.") == ["Aldous is a laureate."]


def test_does_not_split_non_copular_relative():
    # "The records, which are stored..., are public" -> left isn't a copular
    # instance, so we must not mangle it.
    s = "The records, which are stored in the archive, are public."
    assert split_statement(s) == [s]


def test_and_split_still_works():
    parts = split_statement("Socrates was a philosopher, and Socrates was human.")
    assert len(parts) == 2 and "philosopher" in parts[0] and "human" in parts[1]


def test_universal_relative_not_split_as_appositive():
    # "Every person who is bound by the oath ..." must not be appositive-split.
    s = "Every person who is bound by the oath publishes their decisions."
    out = split_statement(s)
    assert out == [s]
