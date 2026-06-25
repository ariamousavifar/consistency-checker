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

import re

from .fidelity import fidelity_check
from .fol_parser import KEYWORDS, check_equivalence, parse_fol, tokenize
from .lemmatizer import lemma
from .linguistics import deontic_cue, hedge_cue, quarantine_shape
from .rule_translator import rule_translate
from .schema import (
    ExtractedStatement,
    GateOutcome,
    Proposition,
    StatementType,
    TranslationCandidate,
)
from .vocabulary import Vocabulary, words_of

# Types excluded from the theory before translation. HYPOTHETICAL is NOT here:
# a supposition is translated and carried to the solver as a reductio ASSUMPTION
# (kept out of the asserted-theory consistency base, tested for "assume-opposite
# leads to contradiction"). See solver.verify Step 3.
_EXCLUDED_REASONS = {
    StatementType.NON_PROPOSITIONAL: "not truth-apt (figurative, expressive, or non-assertoric)",
    StatementType.RHETORICAL: "rhetorical; not asserted as a proposition",
    StatementType.ATTRIBUTED: "attributed to someone else; outside the author's own belief set (v1 scope)",
}

_IDENT_RE = re.compile(r"[A-Za-z_]\w*$")


def _skeleton_and_preds(fol: str) -> tuple[list[str], list[str]]:
    """Abstract every predicate name (a name immediately followed by '(') to a
    placeholder, keeping structure, constants and bound vars verbatim. Returns
    (skeleton tokens, predicate names in order)."""
    toks = tokenize(fol)
    skel: list[str] = []
    preds: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if _IDENT_RE.match(t) and t not in KEYWORDS and nxt == "(":
            skel.append("<P>")
            preds.append(t)
        else:
            skel.append(t)
        i += 1
    return skel, preds


def _is_modifier_variant(p: str, q: str) -> bool:
    """True when two predicate names differ only by a restrictive modifier folded
    into one of them: same head noun, and one word-set is a subset of the other
    ('Fellow' vs 'FellowOfAcademy'). 'ResidentOfFrance' vs 'ResidentOfGermany'
    share a head but neither is a subset, so this is False -- they are distinct
    ideas, not a granularity difference."""
    wp = [lemma(w) for w in words_of(p)]
    wq = [lemma(w) for w in words_of(q)]
    if not wp or not wq or wp[0] != wq[0]:
        return False
    sp, sq = set(wp), set(wq)
    return sp != sq and (sp <= sq or sq <= sp)


def _modifier_only_divergence(fol_a: str, fol_b: str) -> bool:
    """Deterministic (no LLM) test for the modifier-folding divergence that the
    NLI judge was meant to catch: two readings with identical logical structure
    whose only difference is a restrictive modifier folded into a predicate name.
    This is not genuine ambiguity, so the gate can resolve it without a judge."""
    skel_a, preds_a = _skeleton_and_preds(fol_a)
    skel_b, preds_b = _skeleton_and_preds(fol_b)
    if skel_a != skel_b or len(preds_a) != len(preds_b):
        return False
    differed = False
    for pa, pb in zip(preds_a, preds_b):
        if pa == pb:
            continue
        if not _is_modifier_variant(pa, pb):
            return False
        differed = True
    return differed


def _validated(fol: str | None, vocab: Vocabulary) -> str | None:
    if not fol:
        return None
    try:
        norm = vocab.normalize_fol(fol)
        parse_fol(norm)
        return norm
    except Exception:
        return None


def run_gate(stmt: ExtractedStatement, llm_fol: str | None, vocab: Vocabulary, judge=None,
             guard_deontic: bool = False) -> Proposition:
    base = dict(
        id=stmt.id,
        type=stmt.type,
        speaker=stmt.speaker,
        original_text=stmt.original_text,
        decontextualized=stmt.decontextualized,
        depends_on=stmt.depends_on,
    )
    # Predicates established by EARLIER statements (snapshot BEFORE this one
    # registers its own via _validated below). Fidelity exempts these from the
    # invention penalty: a reused symbol was grounded when first coined, so a
    # later reuse needn't re-mention its words. See fidelity_check docstring.
    known_preds = set(vocab.predicates)

    if stmt.type in _EXCLUDED_REASONS:
        return Proposition(**base, status=GateOutcome.QUARANTINED, gate_reason=_EXCLUDED_REASONS[stmt.type])

    # Generic / defeasible guard (deterministic): a hedged generalization
    # ("birds typically fly", "ceteris paribus ...") is not a strict universal.
    # Letting it become a `forall` axiom manufactures false contradictions on an
    # exception, so it is quarantined (never silently dropped) before translation.
    hedge = hedge_cue(stmt.decontextualized, stmt.original_text)
    if hedge is not None:
        return Proposition(
            **base,
            status=GateOutcome.QUARANTINED,
            gate_reason=(
                f"defeasible/hedged generalization (cue: '{hedge}'); excluded from the "
                "strict-universal axiom set so a generic with exceptions cannot produce "
                "a false contradiction"
            ),
        )

    # Deontic / is-ought guard (opt-in, pairs with --allow-conditionals). Once
    # prescriptive content is admitted, formalizing 'X should Y' like the fact
    # 'X is Y' manufactures false contradictions against a descriptive claim.
    # Quarantine (never drop) prescriptive statements so the descriptive axiom
    # set stays is-only. A policy exclusion, not a fragment limit -> no shape.
    if guard_deontic:
        deontic = deontic_cue(stmt.decontextualized, stmt.original_text)
        if deontic is not None:
            return Proposition(
                **base,
                status=GateOutcome.QUARANTINED,
                gate_reason=(
                    f"prescriptive/deontic claim (cue: '{deontic}'); excluded to keep "
                    "norms (ought) out of the descriptive (is) axiom set and avoid "
                    "is/ought false contradictions"
                ),
            )

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
            quarantine_shape=quarantine_shape(stmt.decontextualized),
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
        fids = [(c, fidelity_check(c.fol, stmt.decontextualized, known_preds=known_preds)) for c in candidates]
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
            # Both readings are individually faithful but not syntactically
            # equivalent. They often diverge only by a restrictive modifier
            # folded into a predicate name ("magistrate" vs "magistrate of the
            # charter"), which is not real ambiguity.
            #
            # (1) Deterministic, no-LLM resolution: if the two readings have
            # identical logical structure and differ only by such a folded
            # modifier, keep the rule reading so predicate symbols stay
            # consistent across statements. This is reproducible and free.
            if _modifier_only_divergence(candidates[0].fol, candidates[1].fol):
                pick = next((c for c in candidates if c.source == "rule"), candidates[0])
                return Proposition(
                    **base,
                    fol=pick.fol,
                    candidates=candidates,
                    status=GateOutcome.ACCEPTED,
                    confidence=0.75,
                    gate_reason=f"translators diverged only by a folded modifier (same structure); kept the {pick.source} reading (deterministic)",
                )
            # (2) Opt-in NLI fallback for divergences the structural test cannot
            # settle (e.g. different surface phrasing that is still synonymous).
            # Fires ONLY here, on a genuine two-candidate divergence -- never on
            # single-candidate fidelity. A true quantifier/structure ambiguity is
            # judged non-equivalent and preserved. With no judge this is skipped.
            if judge is not None:
                from .verbalizer import verbalize
                try:
                    va, vb = verbalize(candidates[0].fol), verbalize(candidates[1].fol)
                except Exception:
                    va = vb = ""
                if va and vb and judge.equivalent(va, vb):
                    pick = next((c for c in candidates if c.source == "rule"), candidates[0])
                    return Proposition(
                        **base,
                        fol=pick.fol,
                        candidates=candidates,
                        status=GateOutcome.ACCEPTED,
                        confidence=0.75,
                        gate_reason=f"translators diverged; NLI judged the readings equivalent, kept the {pick.source} reading",
                    )
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
    fid = fidelity_check(c.fol, stmt.decontextualized, known_preds=known_preds)
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
        quarantine_shape=quarantine_shape(stmt.decontextualized),
    )
