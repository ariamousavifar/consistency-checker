"""The batch translator drops hard sentences non-deterministically (null FOL).
A per-statement retry re-asks just the failures and recovers the ones a second
attempt translates -- without disturbing the statements that already succeeded.
"""
import json

from src.extraction import LiveTranslator, _parses
from src.schema import ExtractedStatement


class _Vocab:
    predicates: list[str] = []
    constants: list[str] = []


def _stmt(sid, text):
    return ExtractedStatement(id=sid, type="derived_claim", original_text=text, decontextualized=text)


class _StubClient:
    """First (batch) call returns null for s2; the per-statement retry of s2
    returns valid FOL. Records how many calls and which ids each carried."""
    def __init__(self):
        self.calls = []

    def complete_json(self, system, user, reasoning_effort=None):
        payload = json.loads(user)
        ids = [s["id"] for s in payload["statements"]]
        self.calls.append((ids, reasoning_effort))
        if ids == ["s2"]:                       # the retry of the failed statement
            return {"s2": "forall x. (B(x) -> C(x))"}
        return {sid: ("A(c)" if sid == "s1" else None) for sid in ids}


def test_parses_helper():
    assert _parses("forall x. (A(x) -> B(x))")
    assert not _parses(None)
    assert not _parses("forall x (A(x)")        # malformed
    assert not _parses({"not": "a string"})


def test_retry_recovers_a_null_translation():
    client = _StubClient()
    tr = LiveTranslator(client)
    tr.retry = True
    tr.retry_effort = "high"
    result = tr.translate([_stmt("s1", "a"), _stmt("s2", "b")], _Vocab())
    assert result["s1"] == "A(c)"               # untouched success
    assert result["s2"] == "forall x. (B(x) -> C(x))"   # recovered by retry
    # the retry call carried only s2 and used the higher effort
    assert client.calls[-1] == (["s2"], "high")


def test_retry_disabled_leaves_failures_null():
    client = _StubClient()
    tr = LiveTranslator(client)
    tr.retry = False
    result = tr.translate([_stmt("s1", "a"), _stmt("s2", "b")], _Vocab())
    assert result["s2"] is None
    assert all(eff is None or ids != ["s2"] for ids, eff in client.calls)
