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
from pathlib import Path

from .llm_client import LLMClient
from .prompts import EXTRACTION_SYSTEM, TRANSLATION_SYSTEM
from .schema import ExtractedStatement


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

    def extract(self, text: str) -> list[ExtractedStatement]:
        if len(text) > self.max_chars:
            # v1 keeps a single call within free-tier TPM limits; chunked
            # extraction with running context is the planned upgrade.
            text = text[: self.max_chars]
        data = self.client.complete_json(EXTRACTION_SYSTEM, text)
        return [ExtractedStatement(**item) for item in data]


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
    def __init__(self, client: LLMClient, batch_size: int = 8) -> None:
        self.client = client
        self.batch_size = batch_size

    def translate(self, statements: list[ExtractedStatement], vocabulary) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for i in range(0, len(statements), self.batch_size):
            batch = statements[i : i + self.batch_size]
            payload = {
                "vocabulary": {
                    "predicates": vocabulary.predicates,
                    "constants": vocabulary.constants,
                },
                "statements": [{"id": s.id, "text": s.decontextualized} for s in batch],
            }
            data = self.client.complete_json(TRANSLATION_SYSTEM, json.dumps(payload))
            for s in batch:
                result[s.id] = data.get(s.id)
        return result
