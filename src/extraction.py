"""Extraction judge and LLM translator providers.

Two interchangeable implementations of each:
- Live*: calls the configured OpenAI-compatible endpoint (Groq / NVIDIA NIM).
- Fixture*: loads pre-computed judgments from JSON files, so the full pipeline
  (gate, Z3, reports) runs offline with zero tokens. The shipped fixtures for
  examples/sample_essay.txt were produced by hand to the same schema the live
  prompts request; they double as a frozen regression target for live runs.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .fol_parser import Env, parse_fol
from .llm_client import LLMClient
from .prompts import (
    EXTRACTION_SYSTEM,
    TRANSLATION_SYSTEM,
    TRANSLATION_SYSTEM_CONDITIONALS,
    TRANSLATION_SYSTEM_RELATIONS,
)
from .schema import ExtractedStatement
from .splitter import split_statement

# A predicate head in our FOL fragment is an uppercase-initial CamelCase symbol
# immediately applied to arguments, e.g. Mortal(x), PublishDecision(corin).
# Quantifiers, connectives and constants are all lowercase, so this is unambiguous.
_PREDICATE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\s*\(")


def _predicate_names(fol: str | None) -> list[str]:
    if not fol:
        return []
    return _PREDICATE_RE.findall(fol)


def _parses(fol) -> bool:
    """True if `fol` is a syntactically valid FOL string. Used by the translation
    retry to decide which statements failed (null or malformed). A fresh Env
    declares symbols on the fly, so this checks syntax, not vocabulary alignment
    (that is the gate's job)."""
    if not fol or not isinstance(fol, str):
        return False
    try:
        parse_fol(fol, Env())
        return True
    except Exception:
        return False


def apply_compound_splitting(statements: list[ExtractedStatement]) -> list[ExtractedStatement]:
    """Deterministically split any decontextualized proposition that still
    joins two independent clauses (the LLM often ignores the prompt rule).
    Split children keep the parent's type/speaker and get suffixed ids."""
    out: list[ExtractedStatement] = []
    for st in statements:
        if not isinstance(st, ExtractedStatement):
            # defensive: skip anything that isn't a real statement object
            continue
        pieces = split_statement(st.decontextualized)
        if len(pieces) <= 1:
            out.append(st)
            continue
        for i, piece in enumerate(pieces, start=1):
            out.append(
                ExtractedStatement(
                    id=f"{st.id}.{i}",
                    type=st.type,
                    original_text=st.original_text,
                    decontextualized=piece,
                    speaker=st.speaker,
                    depends_on=st.depends_on,
                )
            )
    return out


class FixtureExtractor:
    def __init__(self, fixtures_dir: Path, source_name: str) -> None:
        self.path = Path(fixtures_dir) / f"{source_name}.extraction.json"

    def extract(self, text: str) -> list[ExtractedStatement]:
        if not self.path.exists():
            raise FileNotFoundError(
                f"No extraction fixture at {self.path}. Offline mode needs fixtures; "
                "run live mode (set LLM_API_KEY) for new documents."
            )
        data = json.loads(self.path.read_text())
        return [ExtractedStatement(**item) for item in data]


class LiveExtractor:
    def __init__(self, client: LLMClient, max_chars: int = 6000) -> None:
        self.client = client
        self.max_chars = max_chars
        # Per-stage reasoning override. Extraction is segmentation, not deep
        # inference, so it can run lean: LLM_EXTRACTION_EFFORT=low keeps it under
        # the token budget (the empty-JSON / TPM-413 failures on dense chunks)
        # while translation can still run at medium. None -> use client default.
        self.effort = os.getenv("LLM_EXTRACTION_EFFORT") or None

    def extract(self, text: str) -> list[ExtractedStatement]:
        if len(text) > self.max_chars:
            # v1 keeps a single call within free-tier TPM limits; chunked
            # extraction with running context is the planned upgrade.
            text = text[: self.max_chars]
        data = self.client.complete_json(EXTRACTION_SYSTEM, text, reasoning_effort=self.effort)
        from .normalize import parse_statements
        return parse_statements(data)


class FixtureTranslator:
    def __init__(self, fixtures_dir: Path, source_name: str) -> None:
        self.path = Path(fixtures_dir) / f"{source_name}.translation.json"
        self._map: dict[str, str | None] | None = None

    def translate(self, statements: list[ExtractedStatement], vocabulary) -> dict[str, str | None]:
        if self._map is None:
            if not self.path.exists():
                raise FileNotFoundError(f"No translation fixture at {self.path}.")
            self._map = json.loads(self.path.read_text())
        return {s.id: self._map.get(s.id) for s in statements}


class LiveTranslator:
    def __init__(self, client: LLMClient, batch_size: int = 8, allow_conditionals: bool = False,
                 allow_relations: bool = False) -> None:
        self.client = client
        self.batch_size = batch_size
        # Opt-in prompts, most permissive wins. --allow-relations admits binary
        # relations (and includes the conditional/deontic handling); fall back to
        # conditionals, then the strict unary base. Off by default so existing
        # runs are byte-identical.
        if allow_relations:
            self.system = TRANSLATION_SYSTEM_RELATIONS
        elif allow_conditionals:
            self.system = TRANSLATION_SYSTEM_CONDITIONALS
        else:
            self.system = TRANSLATION_SYSTEM
        # Per-stage reasoning override. Translation (esp. with conditionals) is
        # where deep reasoning pays off, so it can run hotter than extraction:
        # LLM_TRANSLATION_EFFORT=medium. None -> use client default.
        self.effort = os.getenv("LLM_TRANSLATION_EFFORT") or None
        # Per-statement retry on null/unparseable output (default on; disable with
        # LLM_TRANSLATION_RETRY=0). The batch translator drops hard sentences
        # NON-DETERMINISTICALLY -- a conditional premise returns null in one run
        # and valid FOL in another. Re-asking JUST the failures, one at a time,
        # recovers many of them: an isolated single-statement prompt with the full
        # accumulated vocabulary and a fresh sampling draw. Effort defaults to
        # `medium` (the recovery-vs-cost sweet spot; `high` roughly doubles cost
        # for marginal extra recall -- see LLM_TRANSLATION_RETRY_EFFORT). Only
        # overwrites when the retry actually parses; a still-failing retry leaves
        # the original for the gate to quarantine.
        self.retry = os.getenv("LLM_TRANSLATION_RETRY", "1").strip().lower() not in ("0", "false", "no", "off")
        self.retry_effort = os.getenv("LLM_TRANSLATION_RETRY_EFFORT", "medium") or None

    def _register(self, fol, known: list[str], seen: set[str]) -> None:
        for pred in _predicate_names(fol):
            if pred not in seen:
                seen.add(pred)
                known.append(pred)

    def translate(self, statements: list[ExtractedStatement], vocabulary) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        # Predicate-consistency feedback: batches are translated separately, so a
        # rule in batch 1 ("...must publish their decisions" -> PublishDecision)
        # and its negation in a later batch ("Corin does not publish...") would
        # otherwise be coined as unrelated predicates and never contradict. We
        # accumulate every predicate the model actually emits and feed the growing
        # inventory forward, so later statements reuse earlier symbols. Order is
        # preserved and duplicates dropped to keep the prompt vocabulary stable.
        known: list[str] = list(vocabulary.predicates)
        seen: set[str] = set(known)
        for i in range(0, len(statements), self.batch_size):
            batch = statements[i : i + self.batch_size]
            payload = {
                "vocabulary": {
                    "predicates": known,
                    "constants": vocabulary.constants,
                },
                "statements": [{"id": s.id, "text": s.decontextualized} for s in batch],
            }
            data = self.client.complete_json(self.system, json.dumps(payload), reasoning_effort=self.effort)
            for s in batch:
                fol = data.get(s.id)
                result[s.id] = fol
                self._register(fol, known, seen)

        # Retry pass: re-ask each statement that produced no parseable FOL, one at
        # a time at a higher reasoning effort, with the full accumulated vocabulary.
        if self.retry:
            failed = [s for s in statements if not _parses(result.get(s.id))]
            if failed:
                print(f"  [translate-retry] re-asking {len(failed)} unparsed statement(s) "
                      f"individually...")
                for s in failed:
                    payload = {
                        "vocabulary": {"predicates": known, "constants": vocabulary.constants},
                        "statements": [{"id": s.id, "text": s.decontextualized}],
                    }
                    try:
                        data = self.client.complete_json(
                            self.system, json.dumps(payload), reasoning_effort=self.retry_effort,
                        )
                    except Exception:
                        continue
                    fol = data.get(s.id)
                    if _parses(fol):     # only replace a failure with a real success
                        result[s.id] = fol
                        self._register(fol, known, seen)
        return result
