"""Hybrid translation gate.

Per statement:
  1. Statement types outside the author's asserted belief set are quarantined
     (with distinct reasons), never silently dropped.
  2. The rule translator and the LLM translator each produce a candidate;
     both candidates are normalized into the shared vocabulary and validated
     by the FOL parser.
  3. Two candidates -> Z3 proves or refutes their logical equivalence.
       equivalent          -> accept (high confidence)
       divergent/unknown   -> fidelity check on each candidate:
                                exactly one passes -> accept it
                                both pass          -> flag genuinely ambiguous
                                none pass          -> quarantine
  4. One candidate -> accept only if the fidelity check passes.
Nothing ambiguous or quarantined ever reaches the solver.
"""
from __future__ import annotations

from .fidelity import fidelity_check
from .fol_parser import check_equivalence, parse_fol
from .rule_translator import rule_translate
from .schema import (
    ExtractedStatement,
    GateOutcome,
    Proposition,
    StatementType,
    TranslationCandidate,
)
from .vocabulary import Vocabulary

_EXCLUDED_REASONS = {
    StatementType.NON_PROPOSITIONAL: "not truth-apt (figurative, expressive, or non-assertoric)",
    StatementType.RHETORICAL: "rhetorical; not asserted as a proposition",
    StatementType.ATTRIBUTED: "attributed to someone else; outside the author's own belief set (v1 scope)",
    StatementType.HYPOTHETICAL: "hypothetical or supposed, not asserted (v1 scope)",
}


def _validated(fol: str | None, vocab: Vocabulary) -> str | None:
    if not fol:
        return None
    try:
        norm = vocab.normalize_fol(fol)
        parse_fol(norm)
        return norm
    except Exception:
        return None


def run_gate(stmt: ExtractedStatement, llm_fol: str | None, vocab: Vocabulary) -> Proposition:
    base = dict(
        id=stmt.id,
        type=stmt.type,
        speaker=stmt.speaker,
        original_text=stmt.original_text,
        decontextualized=stmt.decontextualized,
        depends_on=stmt.depends_on,
    )

    if stmt.type in _EXCLUDED_REASONS:
        return Proposition(**base, status=GateOutcome.QUARANTINED, gate_reason=_EXCLUDED_REASONS[stmt.type])

    candidates: list[TranslationCandidate] = []
    rule_fol = _validated(rule_translate(stmt.decontextualized, vocab), vocab)
    if rule_fol:
        candidates.append(TranslationCandidate(source="rule", fol=rule_fol))
    llm_norm = _validated(llm_fol, vocab)
    if llm_norm:
        candidates.append(TranslationCandidate(source="llm", fol=llm_norm))

    if not candidates:
        return Proposition(
            **base,
            status=GateOutcome.QUARANTINED,
            gate_reason="no translator produced valid FOL (outside rule fragment; LLM candidate absent or unparseable)",
        )

    if len(candidates) == 2:
        a, b = candidates[0].fol, candidates[1].fol
        eq = "equivalent" if a == b else check_equivalence(a, b)
        if eq == "equivalent":
            return Proposition(
                **base,
                fol=candidates[0].fol,
                candidates=candidates,
                status=GateOutcome.ACCEPTED,
                confidence=0.95,
                gate_reason="rule and LLM translations independently agree (Z3-proved equivalent)",
            )
        fids = [(c, fidelity_check(c.fol, stmt.decontextualized)) for c in candidates]
        passing = [(c, f) for c, f in fids if f.passed]
        if len(passing) == 1:
            c, f = passing[0]
            return Proposition(
                **base,
                fol=c.fol,
                candidates=candidates,
                status=GateOutcome.ACCEPTED,
                confidence=0.75,
                gate_reason=f"translators diverged; only the {c.source} candidate passed fidelity (coverage {f.coverage})",
            )
        if len(passing) == 2:
            return Proposition(
                **base,
                candidates=candidates,
                status=GateOutcome.AMBIGUOUS,
                gate_reason="non-equivalent translations both pass fidelity: genuine source ambiguity; excluded from axiom set and reported",
            )
        return Proposition(
            **base,
            candidates=candidates,
            status=GateOutcome.QUARANTINED,
            gate_reason="translators diverged and no candidate passed fidelity",
        )

    c = candidates[0]
    fid = fidelity_check(c.fol, stmt.decontextualized)
    if fid.passed:
        return Proposition(
            **base,
            fol=c.fol,
            candidates=candidates,
            status=GateOutcome.ACCEPTED,
            confidence=0.8 if c.source == "rule" else 0.7,
            gate_reason=f"single {c.source} candidate passed fidelity (coverage {fid.coverage})",
        )
    return Proposition(
        **base,
        candidates=candidates,
        status=GateOutcome.QUARANTINED,
        gate_reason=f"single {c.source} candidate failed fidelity (coverage {fid.coverage}; missing {fid.missing})",
    )
