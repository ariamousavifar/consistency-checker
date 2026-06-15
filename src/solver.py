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


def verify(props: list[Proposition], timeout_ms: int = 8000, effort: int = 1) -> list[ClusterReport]:
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
        # Every accepted statement participates in the consistency base.
        all_ids = [p.id for p in cluster if p.id in formulas]
        base = {pid: formulas[pid] for pid in all_ids}
        # The axiom/bridge distinction is kept ONLY for entailment direction.
        given_ids = [p.id for p in cluster
                     if p.type in (StatementType.AXIOM, StatementType.BRIDGE) and p.id in formulas]
        claims = [p for p in cluster
                  if p.type not in (StatementType.AXIOM, StatementType.BRIDGE) and p.id in formulas]

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

        # ---- Step 2: the set is consistent, so classify each claim by ----
        # entailment from the GIVEN statements (axioms + bridges).
        given = {pid: base[pid] for pid in given_ids}
        for p in claims:
            f = formulas[p.id]
            # entailment: given + not(claim) unsat
            s = z3.Solver()
            s.set("timeout", timeout_ms)
            gtrack: dict[str, str] = {}
            for pid, gf in given.items():
                b = z3.Bool(f"g_{pid}")
                gtrack[str(b)] = pid
                s.assert_and_track(gf, b)
            s.push()
            s.add(z3.Not(f))
            r1 = s.check()
            core1 = [gtrack[str(b)] for b in s.unsat_core()] if r1 == z3.unsat else []
            s.pop()
            if r1 == z3.unsat:
                p.verdict = Verdict.ENTAILED
                p.support = _minimize(core1, given, [z3.Not(f)], timeout_ms)
                continue
            # not entailed: consistent with the set (we already know the whole
            # set is SAT, so the claim cannot contradict the givens here).
            # Item 10: flag that the author's premises are insufficient.
            if r1 == z3.unknown:
                p.verdict = Verdict.UNKNOWN
            else:
                p.verdict = Verdict.NOT_ENTAILED
                p.gate_reason = (p.gate_reason + " | " if p.gate_reason else "") + (
                    "premises insufficient to prove this claim; may rely on unstated assumptions"
                )

        reports.append(report)

    return reports
