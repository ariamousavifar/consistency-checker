"""Ingestion and cleaning (v1: plain text).

Deterministic, provenance-preserving. v1 handles .txt input: whitespace
normalization metadata, paragraph segmentation, transcript speaker labels
("Name: ..."), and a whitespace-tolerant span finder so every statement can be
traced to character offsets in the ORIGINAL file even when sentences wrap
across lines. PDF/HTML ingestion (PyMuPDF, trafilatura) is a planned module
behind the same interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import SourceSpan

_SPEAKER = re.compile(r"^([A-Z][A-Za-z .'-]{0,40}):\s+(.*)$")


@dataclass
class Paragraph:
    start: int
    end: int
    speaker: str
    text: str


@dataclass
class CleanDocument:
    raw_text: str
    paragraphs: list[Paragraph] = field(default_factory=list)

    def find_span(self, snippet: str) -> SourceSpan | None:
        """Locate a snippet in the raw text, tolerating arbitrary whitespace runs."""
        pattern = r"\s+".join(re.escape(w) for w in snippet.split())
        m = re.search(pattern, self.raw_text)
        if m:
            return SourceSpan(start=m.start(), end=m.end())
        return None


def clean(raw_text: str) -> CleanDocument:
    doc = CleanDocument(raw_text=raw_text)
    pos = 0
    current_speaker = "author"
    for block in re.split(r"\n\s*\n", raw_text):
        if not block.strip():
            pos += len(block) + 2
            continue
        start = raw_text.find(block, pos)
        end = start + len(block)
        pos = end
        m = _SPEAKER.match(block.strip())
        if m:
            current_speaker = m.group(1).strip()
            text = m.group(2)
        else:
            text = block
        doc.paragraphs.append(
            Paragraph(start=start, end=end, speaker=current_speaker, text=re.sub(r"\s+", " ", text).strip())
        )
    return doc
