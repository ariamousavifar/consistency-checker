"""Resumable chunked extraction (architecture: chunking + extraction + resume).

Runs the extraction (and compound-splitting + instance-retyping) over each chunk
independently, saving each chunk's result to disk as it completes. If the run
dies partway - rate limit, timeout, machine sleep - rerunning with resume=True
reloads the finished chunks and only processes the missing ones, instead of
redoing everything.

After all chunks are extracted, statements are pooled and DEDUPLICATED (chunk
overlap repeats boundary paragraphs, so the same statement can be extracted
twice). Dedup is by normalized decontextualized text. IDs are then renumbered
globally (s1..sN) so the rest of the pipeline - which assumes unique sequential
ids - is unaffected. All pooled statements feed ONE solver run downstream, so
cross-chunk contradictions are still found.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .chunking import chunk_document
from .cleaning import CleanDocument
from .extraction import apply_compound_splitting
from .normalize import retype_bare_instances
from .schema import ExtractedStatement


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".")


def _chunk_cache_path(chunks_dir: Path, index: int) -> Path:
    return chunks_dir / f"chunk_{index:03d}.json"


def _save_chunk(chunks_dir: Path, index: int, statements: list[ExtractedStatement]) -> None:
    chunks_dir.mkdir(parents=True, exist_ok=True)
    data = [s.model_dump() for s in statements]
    _chunk_cache_path(chunks_dir, index).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_chunk(chunks_dir: Path, index: int) -> list[ExtractedStatement] | None:
    p = _chunk_cache_path(chunks_dir, index)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [ExtractedStatement(**d) for d in data]
    except Exception:
        return None


def extract_chunked(
    doc: CleanDocument,
    extractor,
    out_dir: Path,
    *,
    offline: bool,
    resume: bool = False,
    no_chunk: bool = False,
    max_chars: int = 1500,
    overlap_units: int = 1,
    chunk_threshold: int = 2000,
    log=print,
) -> tuple[list[ExtractedStatement], int]:
    """Extract statements from a document, chunking long ones and caching each
    chunk so the work is resumable.

    Returns (statements, num_chunks). num_chunks is 1 for the single-pass path.

    Short documents (raw text under chunk_threshold) take the simple single-pass
    path - no chunk files, identical behavior to before - so nothing changes for
    the existing examples. Only genuinely long documents get chunked. Passing
    no_chunk=True forces single-pass even on long documents (the control
    condition for measuring chunking overhead)."""
    chunks_dir = out_dir / "chunks"

    # Single-pass path for short documents OR when chunking is explicitly disabled.
    if no_chunk or len(doc.raw_text) < chunk_threshold:
        statements = extractor.extract(doc.raw_text)
        if not offline:
            statements = apply_compound_splitting(statements)
        return retype_bare_instances(statements), 1

    chunks = chunk_document(doc, max_chars=max_chars, overlap_units=overlap_units)
    log(f"  [chunking] {len(doc.raw_text)} chars -> {len(chunks)} chunks")

    per_chunk: list[list[ExtractedStatement]] = []
    for ch in chunks:
        cached = _load_chunk(chunks_dir, ch.index) if resume else None
        if cached is not None:
            log(f"  [chunk {ch.index + 1}/{len(chunks)}] resumed from cache "
                f"({len(cached)} statements)")
            per_chunk.append(cached)
            continue
        log(f"  [chunk {ch.index + 1}/{len(chunks)}] extracting...")
        stmts = extractor.extract(ch.text)
        if not offline:
            stmts = apply_compound_splitting(stmts)
        stmts = retype_bare_instances(stmts)
        _save_chunk(chunks_dir, ch.index, stmts)
        per_chunk.append(stmts)

    # Pool + dedup by normalized decontextualized text, renumber ids globally.
    pooled: list[ExtractedStatement] = []
    seen: set[str] = set()
    counter = 0
    for stmts in per_chunk:
        for s in stmts:
            key = _norm(s.decontextualized)
            if not key or key in seen:
                continue
            seen.add(key)
            counter += 1
            s.id = f"s{counter}"
            pooled.append(s)
    log(f"  [chunking] pooled {counter} unique statements "
        f"(from {sum(len(c) for c in per_chunk)} pre-dedup)")
    return pooled, len(chunks)
