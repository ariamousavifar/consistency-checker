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


def verify(props: list[Proposition], timeout_ms: int = 8000) -> list[ClusterReport]:
    accepted = [p for p in props if p.status == GateOutcome.ACCEPTED and p.fol]
    reports: list[ClusterReport] = []

    for idx, cluster in enumerate(cluster_propositions(accepted)):
        report = ClusterReport(cluster_id=idx, statement_ids=[p.id for p in cluster])
        env = Env()
        formulas: dict[str, z3.ExprRef] = {}
        for p in cluster:
            try:
                formulas[p.id], _ = parse_fol(p.fol, env)
            except Exception as exc:
                p.verdict = Verdict.ERROR
                p.gate_reason += f" | FOL parse failed at solver stage: {exc}"

        axioms = {p.id: formulas[p.id] for p in cluster if p.type == StatementType.AXIOM and p.id in formulas}
        claims = [p for p in cluster if p.type != StatementType.AXIOM and p.id in formulas]

        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        tracker: dict[str, str] = {}
        for pid, f in axioms.items():
            b = z3.Bool(f"track_{pid}")
            tracker[str(b)] = pid
            solver.assert_and_track(f, b)

        res = solver.check()
        if res == z3.unsat:
            core = [tracker[str(b)] for b in solver.unsat_core()]
            mus = _minimize(core, axioms, [], timeout_ms)
            report.axioms_consistent = False
            report.axiom_conflict = mus
            report.note = "axiom set is internally inconsistent; claim verdicts withheld in this cluster"
            for p in cluster:
                if p.id in mus:
                    p.verdict = Verdict.CONTRADICTS
                    p.conflict = [x for x in mus if x != p.id]
                elif p.type != StatementType.AXIOM:
                    p.verdict = Verdict.UNKNOWN
            reports.append(report)
            continue

        report.axioms_consistent = res == z3.sat
        if res == z3.unknown:
            report.note = "solver could not certify axiom satisfiability (quantified SAT is hard); proceeding with claim checks"

        for p in claims:
            f = formulas[p.id]
            solver.push()
            solver.add(z3.Not(f))
            r1 = solver.check()
            core1 = [tracker[str(b)] for b in solver.unsat_core()] if r1 == z3.unsat else []
            solver.pop()
            if r1 == z3.unsat:
                p.verdict = Verdict.ENTAILED
                p.support = _minimize(core1, axioms, [z3.Not(f)], timeout_ms)
                continue
            solver.push()
            solver.add(f)
            r2 = solver.check()
            core2 = [tracker[str(b)] for b in solver.unsat_core()] if r2 == z3.unsat else []
            solver.pop()
            if r2 == z3.unsat:
                p.verdict = Verdict.CONTRADICTS
                p.conflict = _minimize(core2, axioms, [f], timeout_ms)
            elif r1 == z3.sat and r2 == z3.sat:
                p.verdict = Verdict.NOT_ENTAILED
            else:
                p.verdict = Verdict.UNKNOWN

        reports.append(report)

    return reports
