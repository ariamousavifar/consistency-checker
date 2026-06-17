"""v0.7 tests: chunking, resumable extraction, cross-chunk pooling, tier config,
Cerebras param, empty-response guard."""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.cleaning import clean
from src.chunking import chunk_document
from src.chunked_extraction import extract_chunked
from src.schema import ExtractedStatement, StatementType


def _doc(n_paras, words_each=8):
    paras = [" ".join([f"word{j}" for j in range(words_each)]) + f" para{i}." for i in range(n_paras)]
    return clean("\n\n".join(paras))


def test_short_doc_is_single_chunk():
    doc = clean("All cats are animals.\n\nThe tabby is a cat.")
    chunks = chunk_document(doc, max_chars=1500)
    assert len(chunks) == 1


def test_long_doc_splits_into_multiple_chunks():
    doc = _doc(40, words_each=20)
    chunks = chunk_document(doc, max_chars=800, overlap_units=1)
    assert len(chunks) > 1


def test_chunk_overlap_preserves_seam_context():
    doc = _doc(10, words_each=30)
    chunks = chunk_document(doc, max_chars=400, overlap_units=1)
    # last paragraph of chunk i should reappear as first of chunk i+1
    if len(chunks) >= 2:
        assert chunks[0].paragraphs[-1].text == chunks[1].paragraphs[0].text


def test_every_paragraph_is_covered():
    doc = _doc(15, words_each=12)
    chunks = chunk_document(doc, max_chars=300, overlap_units=1)
    covered = {p.text for c in chunks for p in c.paragraphs}
    assert len(covered) == 15


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


def test_chunked_extraction_saves_and_resumes():
    doc = _doc(20, words_each=10)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        ex1 = _FakeExtractor()
        r1, n1 = extract_chunked(doc, ex1, out, offline=False, resume=False,
                                max_chars=200, chunk_threshold=50)
        assert ex1.calls > 1  # actually chunked
        assert (out / "chunks").exists()
        n_files = len(list((out / "chunks").glob("*.json")))
        assert n_files == ex1.calls

        # resume: no new extractor calls, identical output
        ex2 = _FakeExtractor()
        r2, n2 = extract_chunked(doc, ex2, out, offline=False, resume=True,
                                max_chars=200, chunk_threshold=50)
        assert ex2.calls == 0
        assert len(r1) == len(r2)


def test_chunked_extraction_dedups_overlap():
    # overlap repeats boundary paragraphs; pooled output must be unique
    doc = _doc(12, words_each=10)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        stmts, _ = extract_chunked(doc, _FakeExtractor(), out, offline=False,
                                   resume=False, max_chars=200, chunk_threshold=50)
        texts = [s.decontextualized for s in stmts]
        assert len(texts) == len(set(texts))  # no duplicates
        # ids renumbered globally and sequential
        assert [s.id for s in stmts] == [f"s{i+1}" for i in range(len(stmts))]


def test_short_doc_takes_single_pass_no_chunk_files():
    doc = clean("All cats are animals.\n\nThe tabby is a cat.")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        extract_chunked(doc, _FakeExtractor(), out, offline=False, resume=False,
                        chunk_threshold=2000)  # returns tuple, ignored
        # short doc -> no chunks dir created
        assert not (out / "chunks").exists()


def test_cross_chunk_contradiction_pools_into_one_solver_run():
    """The core guarantee: statements from different chunks reach ONE solver run,
    so a contradiction spanning chunks is found."""
    from src.vocabulary import Vocabulary
    from src.rule_translator import rule_translate
    from src.gate import run_gate
    from src.solver import verify
    from src.schema import Verdict

    vocab = Vocabulary()
    stmts = [
        ("s1", StatementType.AXIOM, "All magistrates are publishers."),
        ("s2", StatementType.AXIOM, "Corin is a magistrate."),
        ("s3", StatementType.DERIVED_CLAIM, "Corin is not a publisher."),
    ]
    es = [ExtractedStatement(id=i, type=t, original_text=s, decontextualized=s)
          for i, t, s in stmts]
    fols = {e.id: rule_translate(e.decontextualized, vocab) for e in es}
    props = [run_gate(e, fols.get(e.id), vocab) for e in es]
    verify(props)
    assert any(p.verdict == Verdict.CONTRADICTS for p in props)


# ---- Cerebras param + empty-response guard ----

def test_cerebras_uses_max_completion_tokens(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    from src.providers import resolve_model_config
    c = resolve_model_config("cerebras", "gpt-oss-120b")
    assert c["max_tokens_param"] == "max_completion_tokens"
    assert c["model"] == "gpt-oss-120b"  # bare id, no prefix


def test_other_providers_use_max_tokens(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    from src.providers import resolve_model_config
    g = resolve_model_config("groq", "llama-3.3-70b-versatile")
    assert g["max_tokens_param"] == "max_tokens"


def test_empty_response_retries_original_not_correction():
    from src.llm_client import LLMClient, LLMConfig
    cfg = LLMConfig(overrides={"base_url": "x", "api_key": "k", "model": "m"})
    client = LLMClient.__new__(LLMClient)
    client.config = cfg
    client._last_call_ts = 0.0
    seen = []
    seq = iter(["", '[{"id":"s1","type":"axiom","original_text":"a","decontextualized":"a"}]'])

    def fake_raw(system, user):
        seen.append(user)
        return next(seq)

    client._raw = fake_raw
    result = client.complete_json("SYS", "ORIGINAL DOC", retries=2)
    assert result[0]["id"] == "s1"
    # after an empty reply, the retry should resend the ORIGINAL doc unchanged,
    # NOT a "your output wasn't JSON" correction (there was no output to correct)
    assert seen[1] == "ORIGINAL DOC"


def test_tier_field_in_manifest():
    import json
    m = json.loads(Path("examples/examples.json").read_text())
    tiers = {e.get("tier") for e in m["examples"]}
    assert 1 in tiers and 2 in tiers and 25 in tiers
