"""Document chunking (architecture stage 1.5, between cleaning and extraction).

Long documents (real Wikipedia pages, SEP entries, debate transcripts) overflow
a single extraction call: the input may be truncated, the output may exceed the
token budget, and one failure loses the whole document. Chunking splits a
CleanDocument into bounded pieces, each extracted independently. All resulting
statements are pooled into ONE proposition store before the solver runs, so a
contradiction spanning chunk 1 and chunk 9 is still caught - the solver sees the
whole belief set at once. Only the LLM stages scale with length; Z3 does not
care how many statements there are.

Design notes:
- Boundary detection is PLUGGABLE. v0.7 ships a paragraph-boundary detector.
  A transcript-aware detector (reassembling fragmented caption lines into
  sentences and splitting on speaker turns) drops in later behind the same
  `boundaries()` interface without touching the pipeline.
- Chunks never split a paragraph; we group whole paragraphs up to a character
  budget. A small paragraph OVERLAP between consecutive chunks preserves context
  at the seams so a pronoun near a boundary still has its referent available to
  the decontextualizing rewriter.
- Each chunk records the source character span of its first/last paragraph for
  provenance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .cleaning import CleanDocument, Paragraph


@dataclass
class Chunk:
    index: int
    text: str
    paragraphs: list[Paragraph] = field(default_factory=list)
    start: int = 0
    end: int = 0


def paragraph_boundaries(doc: CleanDocument) -> list[Paragraph]:
    """Default boundary unit: one cleaned paragraph. Pluggable seam for a future
    transcript-aware unit (sentence-reassembled, speaker-split)."""
    return list(doc.paragraphs)


def chunk_document(
    doc: CleanDocument,
    max_chars: int = 1500,
    overlap_units: int = 1,
    boundaries=paragraph_boundaries,
) -> list[Chunk]:
    """Group boundary units (paragraphs by default) into chunks under max_chars.

    max_chars is a soft cap on the joined text of a chunk; a single unit larger
    than the cap becomes its own chunk rather than being split (loyalty to the
    text - we never cut inside a paragraph). overlap_units repeats the last N
    units of the previous chunk at the start of the next so context isn't lost
    at a seam.
    """
    units = boundaries(doc)
    if not units:
        # whole-document fallback so empty-paragraph docs still process
        return [Chunk(index=0, text=doc.raw_text.strip(),
                      start=0, end=len(doc.raw_text))]

    chunks: list[Chunk] = []
    cur: list[Paragraph] = []
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if not cur:
            return
        text = "\n\n".join(p.text for p in cur)
        chunks.append(Chunk(
            index=len(chunks),
            text=text,
            paragraphs=list(cur),
            start=cur[0].start,
            end=cur[-1].end,
        ))
        # seed next chunk with overlap units for seam context
        cur = cur[-overlap_units:] if overlap_units > 0 else []
        cur_len = sum(len(p.text) for p in cur)

    for unit in units:
        ulen = len(unit.text)
        if cur and cur_len + ulen > max_chars:
            flush()
        cur.append(unit)
        cur_len += ulen
    # final flush WITHOUT re-seeding overlap
    if cur:
        # avoid emitting a trailing chunk that is ONLY overlap (already covered)
        if not (len(chunks) > 0 and all(p in chunks[-1].paragraphs for p in cur)):
            text = "\n\n".join(p.text for p in cur)
            chunks.append(Chunk(
                index=len(chunks),
                text=text,
                paragraphs=list(cur),
                start=cur[0].start,
                end=cur[-1].end,
            ))
    return chunks
