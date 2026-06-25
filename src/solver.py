"""Symbolic verification layer.

Steps per run:
  1. Cluster accepted propositions by shared predicates (connected components),
     so one bad cluster cannot poison unrelated statements.
  2. Per cluster: check the axiom set for consistency with tracked assertions.
     If inconsistent, shrink the unsat core to a minimal conflicting set and
     report it; claims in that cluster become UNKNOWN (anything follows from
     an inconsistent set, so verdicts would be meaningless).
  3. Per claim: entailment = axioms + not(claim) unsat (support set reported,
     minimized); contradiction = axioms + claim unsat (conflict set reported,
     minimized); satisfiable both ways = not_entailed; solver timeout = UNKNOWN.
UNKNOWN is a first-class verdict, never collapsed into error.
"""
from __future__ import annotations

import re
import time as _time

import z3

from . import forward_chain
from .fol_parser import Env, parse_fol, tokenize, KEYWORDS
from .schema import ClusterReport, GateOutcome, Proposition, StatementType, Verdict


def _predicates_of(fol: str) -> set[str]:
    toks = tokenize(fol)
    preds = set()
    for i, t in enumerate(toks):
        if re.match(r"[A-Za-z_]\w*$", t) and t not in KEYWORDS:
            if i + 1 < len(toks) and toks[i + 1] == "(":
                preds.add(t)
    return preds


def _alpha_key(fol: str) -> str:
    """Canonical, alpha-invariant form of a FOL string: bound variables are
    renamed to a positional sequence so 'forall s. P(s)' and 'forall x. P(x)'
    map to one key, while different predicates/structure stay distinct. Used to
    collapse statements the extractor produced more than once (over-extraction
    yields near-duplicate sentences whose FOL is identical)."""
    toks = tokenize(fol)
    out: list[str] = []
    mapping: dict[str, str] = {}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("forall", "exists") and i + 1 < len(toks):
            out.append(t)
            var = toks[i + 1]
            if var not in mapping:
                mapping[var] = f"#{len(mapping)}"
            out.append(mapping[var])
            i += 2
            continue
        out.append(mapping.get(t, t))
        i += 1
    return " ".join(out)


def mark_duplicate_fols(props: list[Proposition]) -> int:
    """Quarantine accepted statements whose FOL is logically identical to an
    earlier one (same alpha-normalized form), keeping the first as canonical.
    Prevents over-extraction from manufacturing spurious 'X proved from X' tree
    edges and inflating the statement count. Bridges are never deduped (a
    user-supplied premise is kept verbatim). Nothing is dropped silently: the
    duplicate stays in the report, excluded with a reason pointing at its
    canonical. Returns the number marked."""
    seen: dict[str, str] = {}
    marked = 0
    for p in props:
        if (p.status != GateOutcome.ACCEPTED or not p.fol
                or p.type == StatementType.BRIDGE):
            continue
        try:
            key = _alpha_key(p.fol)
        except Exception:
            continue
        if key in seen:
            p.status = GateOutcome.QUARANTINED
            p.gate_reason = (
                f"duplicate of {seen[key]} (identical logical content after normalization); "
                "excluded so over-extraction can't manufacture spurious derivation edges"
            )
            p.quarantine_shape = None
            marked += 1
        else:
            seen[key] = p.id
    return marked


def cluster_propositions(props: list[Proposition]) -> list[list[Proposition]]:
    parent: dict[str, str] = {p.id: p.id for p in props}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    owner: dict[str, str] = {}
    for p in props:
        for pred in _predicates_of(p.fol or ""):
            if pred in owner:
                union(p.id, owner[pred])
            else:
                owner[pred] = p.id

    groups: dict[str, list[Proposition]] = {}
    for p in props:
        groups.setdefault(find(p.id), []).append(p)
    return list(groups.values())


def _minimize(ids: list[str], formulas: dict[str, z3.ExprRef], extra: list[z3.ExprRef], timeout_ms: int) -> list[str]:
    """Deletion-based shrink of an unsat core to a minimal unsatisfiable subset."""
    current = list(ids)
    for pid in list(current):
        trial = [x for x in current if x != pid]
        s = z3.Solver()
        s.set("timeout", timeout_ms)
        for x in trial:
            s.add(formulas[x])
        for f in extra:
            s.add(f)
        if s.check() == z3.unsat:
            current = trial
    return current


def verify(props: list[Proposition], timeout_ms: int = 8000, effort: int = 1,
           prune_derivation: bool = False) -> list[ClusterReport]:
    accepted = [p for p in props if p.status == GateOutcome.ACCEPTED and p.fol]
    reports: list[ClusterReport] = []

    # effort 0: solver skipped entirely (handled by caller). 1: per-cluster.
    # 2+: one global set for deeper cross-topic reasoning.
    clusters = cluster_propositions(accepted) if effort <= 1 else ([accepted] if accepted else [])
    for idx, cluster in enumerate(clusters):
        report = ClusterReport(
            cluster_id=idx, statement_ids=[p.id for p in cluster], n_statements=len(cluster)
        )
        t_cluster = _time.perf_counter()
        env = Env()
        formulas: dict[str, z3.ExprRef] = {}
        for p in cluster:
            try:
                formulas[p.id], _ = parse_fol(p.fol, env)
            except Exception as exc:
                p.verdict = Verdict.ERROR
                p.gate_reason += f" | FOL parse failed at solver stage: {exc}"

        # Item 9: inconsistency is a property of a SET, independent of role.
        # Every ASSERTED statement participates in the consistency base.
        # HYPOTHETICAL suppositions are NOT asserted -- they are reductio
        # assumptions, kept out of the base (else the author's deliberate
        # "assume the opposite" would read as the author contradicting himself)
        # and tested separately in Step 3.
        hypos = [p for p in cluster
                 if p.type == StatementType.HYPOTHETICAL and p.id in formulas]
        all_ids = [p.id for p in cluster
                   if p.id in formulas and p.type != StatementType.HYPOTHETICAL]
        base = {pid: formulas[pid] for pid in all_ids}
        # The axiom/bridge distinction is kept ONLY for entailment direction.
        given_ids = [p.id for p in cluster
                     if p.type in (StatementType.AXIOM, StatementType.BRIDGE) and p.id in formulas]
        claims = [p for p in cluster
                  if p.type not in (StatementType.AXIOM, StatementType.BRIDGE, StatementType.HYPOTHETICAL)
                  and p.id in formulas]

        if effort >= 2:
            report.note = "global set (effort 2): deeper cross-topic reasoning, higher timeout risk"
        # Item 6: warn before Z3's quantifier instantiation becomes unpredictable.
        if len(base) > 40:
            report.note = (report.note + "; " if report.note else "") + (
                f"large set ({len(base)}): quantifier instantiation may slow or return unknown"
            )

        # ---- Step 1: is the whole accepted set mutually consistent? ----
        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        tracker: dict[str, str] = {}
        for pid, f in base.items():
            b = z3.Bool(f"track_{pid}")
            tracker[str(b)] = pid
            solver.assert_and_track(f, b)

        res = solver.check()
        if res == z3.unsat:
            core = [tracker[str(b)] for b in solver.unsat_core()]
            mus = _minimize(core, base, [], timeout_ms)
            report.axioms_consistent = False
            report.axiom_conflict = mus
            report.note = (
                "this set of statements is mutually inconsistent; at least one member "
                "of the minimal set must be abandoned (the system does not pick which)"
            )
            # Reconstruct HOW the contradiction is derived (the chains of theorems
            # that collide), so the inconsistent case shows a derivation, not just
            # a flat unsat-core fan. Forward chaining is constructive, so it works
            # where Z3-entailment cannot (from an inconsistent set everything is
            # "entailed"). Best-effort: None if the clause shapes are unsupported.
            try:
                ref = forward_chain.explain([p for p in cluster if p.id in formulas])
                if ref is not None:
                    report.refutation = forward_chain.serialize(ref, prune=prune_derivation)
            except Exception:
                report.refutation = None
            # The givens MINUS the conflicting members may still be consistent and
            # may still entail some claims. Compute that context so the report can
            # explain WHY the conflict arises, instead of collapsing all to unknown.
            safe_given = {pid: base[pid] for pid in given_ids if pid not in mus}
            ctx = z3.Solver()
            ctx.set("timeout", timeout_ms)
            for gf in safe_given.values():
                ctx.add(gf)
            context_consistent = ctx.check() != z3.unsat
            for p in cluster:
                if p.id in mus:
                    p.verdict = Verdict.CONTRADICTS
                    p.conflict = [x for x in mus if x != p.id]
                elif p.id in formulas:
                    proved = False
                    if context_consistent and p.id not in given_ids:
                        s2 = z3.Solver()
                        s2.set("timeout", timeout_ms)
                        gt: dict[str, str] = {}
                        for pid, gf in safe_given.items():
                            b = z3.Bool(f"sg_{pid}")
                            gt[str(b)] = pid
                            s2.assert_and_track(gf, b)
                        s2.add(z3.Not(formulas[p.id]))
                        if s2.check() == z3.unsat:
                            p.verdict = Verdict.ENTAILED
                            p.support = _minimize(
                                [gt[str(b)] for b in s2.unsat_core()], safe_given,
                                [z3.Not(formulas[p.id])], timeout_ms,
                            )
                            proved = True
                    if not proved:
                        p.verdict = Verdict.UNKNOWN
            report.solver_ms = round((_time.perf_counter() - t_cluster) * 1000, 2)
            reports.append(report)
            continue

        report.axioms_consistent = res == z3.sat
        if res == z3.unknown:
            report.note = "solver could not certify satisfiability (quantified SAT is hard); proceeding with claim checks"

        # ---- Step 2: LAYERED entailment -> reconstruct the derivation TREE. ----
        # The old check tested each claim only against the AXIOMS, so the support
        # graph was a flat FAN: every theorem hung directly off the axioms, with
        # no theorem->theorem edges. We instead grow a `proven` set: once a
        # theorem is established it becomes available as a premise for later ones.
        # When we then minimize a claim's support we seed minimization with the
        # already-proven theorems and strip AXIOMS BEFORE THEOREMS, so the
        # irreducible support kept is the *deepest* one -- the compact intermediate
        # theorem that packages several axioms (T2 supported by {T1, a3}, not by
        # {a1, a2, a3}). That turns the fan into Euclid's tree. Logically the set
        # of entailed claims is unchanged (entailment is transitive); only the
        # support ATTRIBUTION -- i.e. the tree edges -- changes.
        given = {pid: base[pid] for pid in given_ids}
        given_set = set(given_ids)
        proven: dict[str, z3.ExprRef] = dict(given)
        pending = list(claims)

        def _derive_to_fixpoint() -> None:
            """Derive every pending claim that follows from the current `proven`
            set, attributing each to the DEEPEST (most compressed) support, and
            add it to `proven` so later claims can build on it. Repeats until no
            new claim derives."""
            progress = True
            while progress:
                progress = False
                for p in list(pending):
                    f = formulas[p.id]
                    s = z3.Solver()
                    s.set("timeout", timeout_ms)
                    ptrack: dict[str, str] = {}
                    for pid, gf in proven.items():
                        b = z3.Bool(f"p_{pid}")
                        ptrack[str(b)] = pid
                        s.assert_and_track(gf, b)
                    s.push()
                    s.add(z3.Not(f))
                    r1 = s.check()
                    core1 = [ptrack[str(b)] for b in s.unsat_core()] if r1 == z3.unsat else []
                    s.pop()
                    if r1 == z3.unsat:
                        # Seed with the core PLUS every proven THEOREM (non-given),
                        # then order givens first so minimization drops redundant
                        # axioms and keeps the compact intermediate theorem.
                        seed = list(core1) + [pid for pid in proven
                                              if pid not in given_set and pid not in core1]
                        seed.sort(key=lambda x: 0 if x in given_set else 1)
                        p.verdict = Verdict.ENTAILED
                        p.support = _minimize(seed, proven, [z3.Not(f)], timeout_ms)
                        proven[p.id] = f
                        pending.remove(p)
                        progress = True
                    elif r1 == z3.unknown:
                        p.verdict = Verdict.UNKNOWN
                        pending.remove(p)

        # Phase 1: derive everything that follows from the axioms/bridges.
        _derive_to_fixpoint()

        # Phase 2: ASSERTED-PREMISE ROOTS. A claim still pending isn't derivable
        # from the axioms -- but the author may USE it as a premise that other
        # claims follow from (common when the extractor types a foundational
        # premise as `derived_claim`). Promote a pending "source" (a claim not
        # entailed by the rest of the pending set) into the foundation as an
        # asserted premise, then derive its dependents. Repeat. This assembles the
        # argument tree past the axiom layer. Acyclic by construction: a source is
        # promoted only when nothing else entails it; if every remaining claim is
        # mutually entailed (a cycle of equivalents) we break it arbitrarily.
        while pending:
            src = None
            for p in pending:
                others = dict(proven)
                for q in pending:
                    if q.id != p.id:
                        others[q.id] = formulas[q.id]
                s = z3.Solver()
                s.set("timeout", timeout_ms)
                for gf in others.values():
                    s.add(gf)
                s.add(z3.Not(formulas[p.id]))
                if s.check() != z3.unsat:   # not entailed by the others -> a root
                    src = p
                    break
            if src is None:
                src = pending[0]            # mutual-entailment cycle: break it
            src.verdict = Verdict.NOT_ENTAILED
            src.gate_reason = (src.gate_reason + " | " if src.gate_reason else "") + (
                "asserted premise (not derivable from the other statements); a root of the argument"
            )
            proven[src.id] = formulas[src.id]
            pending.remove(src)
            _derive_to_fixpoint()

        # ---- Step 3: REDUCTIO. A hypothetical supposition that contradicts the ----
        # established theory is a successful reductio ad absurdum: its negation is
        # thereby proven. One that is consistent with the theory is an ordinary
        # supposition (a case split), not a contradiction.
        for h in hypos:
            s = z3.Solver()
            s.set("timeout", timeout_ms)
            htrack: dict[str, str] = {}
            for pid, pf in proven.items():
                b = z3.Bool(f"h_{pid}")
                htrack[str(b)] = pid
                s.assert_and_track(pf, b)
            s.add(formulas[h.id])           # assume the supposition
            if s.check() == z3.unsat:
                h.verdict = Verdict.REFUTED
                h.conflict = _minimize(
                    [htrack[str(b)] for b in s.unsat_core()], proven, [formulas[h.id]], timeout_ms,
                )
                h.gate_reason = (h.gate_reason + " | " if h.gate_reason else "") + (
                    "reductio ad absurdum: this supposition contradicts the established "
                    "theory, so its negation is proven"
                )
            else:
                h.verdict = Verdict.NOT_ENTAILED
                h.gate_reason = (h.gate_reason + " | " if h.gate_reason else "") + (
                    "supposition consistent with the theory (a case split); no contradiction follows"
                )

        reports.append(report)

    return reports
