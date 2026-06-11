"""Shared data models for the consistency-checking pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class StatementType(str, Enum):
    AXIOM = "axiom"
    DERIVED_CLAIM = "derived_claim"
    ATTRIBUTED = "attributed"
    HYPOTHETICAL = "hypothetical"
    RHETORICAL = "rhetorical"
    NON_PROPOSITIONAL = "non_propositional"
    BRIDGE = "bridge"


class GateOutcome(str, Enum):
    ACCEPTED = "accepted"
    AMBIGUOUS = "ambiguous"
    QUARANTINED = "quarantined"


class Verdict(str, Enum):
    ENTAILED = "entailed"
    NOT_ENTAILED = "not_entailed"
    CONTRADICTS = "contradicts"
    UNKNOWN = "unknown"
    ERROR = "error"


class SourceSpan(BaseModel):
    start: int
    end: int


class ExtractedStatement(BaseModel):
    """Output of the extraction judge (stage: Extraction judge)."""

    id: str
    type: StatementType
    original_text: str
    decontextualized: str
    speaker: str = "author"
    depends_on: list[str] = Field(default_factory=list)


class TranslationCandidate(BaseModel):
    source: str  # "rule" or "llm"
    fol: str


class Proposition(BaseModel):
    """A row in the proposition store."""

    id: str
    type: StatementType
    speaker: str = "author"
    original_text: str
    decontextualized: str
    span: Optional[SourceSpan] = None
    fol: Optional[str] = None
    candidates: list[TranslationCandidate] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    status: GateOutcome
    confidence: float = 0.0
    gate_reason: str = ""
    verdict: Optional[Verdict] = None
    support: list[str] = Field(default_factory=list)   # axiom ids proving an entailed claim
    conflict: list[str] = Field(default_factory=list)  # minimal conflicting set for a contradiction


class ClusterReport(BaseModel):
    cluster_id: int
    statement_ids: list[str]
    axioms_consistent: Optional[bool] = None
    axiom_conflict: list[str] = Field(default_factory=list)
    note: str = ""


class RunReport(BaseModel):
    source_file: str
    mode: str
    propositions: list[Proposition]
    clusters: list[ClusterReport]
    vocabulary_predicates: list[str]
    vocabulary_constants: list[str]
    effort: int = 1
    timing: list[dict] = Field(default_factory=list)
    screener: list[dict] = Field(default_factory=list)
