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
    # A hypothetical supposition that contradicts the established theory: a
    # successful reductio ad absurdum, so its NEGATION is thereby proven. Kept
    # distinct from CONTRADICTS so the author's deliberate "assume the opposite"
    # move is never reported as the author contradicting himself.
    REFUTED = "refuted"


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
    # For statements quarantined because they fell outside the unary FOL fragment:
    # a coarse heuristic bucket of the expressiveness they need (relational, modal,
    # numeric, ...). Drives the relations-roadmap measurement; None otherwise.
    quarantine_shape: Optional[str] = None
    verdict: Optional[Verdict] = None
    support: list[str] = Field(default_factory=list)   # axiom ids proving an entailed claim
    conflict: list[str] = Field(default_factory=list)  # minimal conflicting set for a contradiction


class ClusterReport(BaseModel):
    cluster_id: int
    statement_ids: list[str]
    axioms_consistent: Optional[bool] = None
    axiom_conflict: list[str] = Field(default_factory=list)
    note: str = ""
    n_statements: int = 0
    solver_calls: int = 0
    solver_ms: float = 0.0
    hit_timeout: bool = False


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
    usage: dict = Field(default_factory=dict)
    chunked: bool = False
    num_chunks: int = 1
