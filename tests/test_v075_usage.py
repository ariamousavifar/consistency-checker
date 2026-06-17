"""v0.7.5 tests: token usage tracking, --no-chunk single-pass control, and the
usage fields on RunReport."""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.cleaning import clean
from src.chunked_extraction import extract_chunked
from src.schema import ExtractedStatement, StatementType


class _FakeExtractor:
    def __init__(self):
        self.calls = 0

    def extract(self, text):
        self.calls += 1
        out = []
        for i, blk in enumerate(text.split("\n\n")):
            blk = blk.strip()
            if blk:
                out.append(ExtractedStatement(
                    id=f"x{i}", type=StatementType.AXIOM,
                    original_text=blk, decontextualized=blk))
        return out


def _long_doc(n=30, words=20):
    paras = [" ".join(f"word{j}" for j in range(words)) + f" para{i}." for i in range(n)]
    return clean("\n\n".join(paras))


def test_extract_chunked_returns_chunk_count():
    doc = _long_doc()
    with tempfile.TemporaryDirectory() as td:
        stmts, n = extract_chunked(doc, _FakeExtractor(), Path(td), offline=False,
                                   resume=False, max_chars=400, chunk_threshold=50)
        assert n > 1  # genuinely chunked


def test_no_chunk_forces_single_pass():
    doc = _long_doc()  # long enough to normally chunk
    with tempfile.TemporaryDirectory() as td:
        ex = _FakeExtractor()
        stmts, n = extract_chunked(doc, ex, Path(td), offline=False, resume=False,
                                   no_chunk=True, max_chars=400, chunk_threshold=50)
        assert n == 1          # forced single pass
        assert ex.calls == 1   # exactly one extraction call
        assert not (Path(td) / "chunks").exists()  # no chunk cache written


def test_short_doc_reports_single_chunk():
    doc = clean("All cats are animals.\n\nThe tabby is a cat.")
    with tempfile.TemporaryDirectory() as td:
        stmts, n = extract_chunked(doc, _FakeExtractor(), Path(td), offline=False,
                                   resume=False, chunk_threshold=2000)
        assert n == 1


def test_llmclient_usage_accumulates():
    from src.llm_client import LLMClient, LLMConfig

    class FakeUsage:
        prompt_tokens = 100
        completion_tokens = 40
        total_tokens = 140

    class FakeMsg:
        content = '[]'
        reasoning_content = None

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]
        usage = FakeUsage()

    cfg = LLMConfig(overrides={"base_url": "x", "api_key": "k", "model": "m", "min_interval": 0})
    client = LLMClient.__new__(LLMClient)
    client.config = cfg
    client._last_call_ts = 0.0
    client.calls = client.prompt_tokens = client.completion_tokens = client.total_tokens = 0
    client._create = lambda **kw: FakeResp()
    client._throttle = lambda: None

    client._raw("sys", "user")
    client._raw("sys", "user")
    u = client.usage()
    assert u["calls"] == 2
    assert u["total_tokens"] == 280
    assert u["prompt_tokens"] == 200
    assert u["completion_tokens"] == 80


def test_runreport_has_usage_fields():
    from src.schema import RunReport
    r = RunReport(source_file="x", mode="m", propositions=[], clusters=[],
                  vocabulary_predicates=[], vocabulary_constants=[])
    assert r.usage == {}
    assert r.chunked is False
    assert r.num_chunks == 1
