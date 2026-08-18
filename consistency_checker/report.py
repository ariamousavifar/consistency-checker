"""Report generation: Markdown + JSON (architecture stage 8).

Language policy: the system identifies minimal inconsistent sets; it never
declares which member of a set is the wrong one. A contradiction verdict means
"this statement belongs to a set that cannot all be true", not "this statement
is false".
"""
from __future__ import annotations

from .schema import GateOutcome, RunReport, StatementType, Verdict
from .tree_builder import build_tree_text
from .verbalizer import verbalize


def _safe_verbalize(fol: str | None) -> str:
    if not fol:
        return ""
    try:
        return verbalize(fol)
    except Exception:
        return "(verbalization failed)"


def render_markdown(report: RunReport) -> str:
    props = report.propositions
    by_id = {p.id: p for p in props}
    lines: list[str] = []
    lines.append("# Consistency report")
    lines.append("")
    lines.append(f"Source: `{report.source_file}` | mode: {report.mode} | effort: {report.effort}")
    lines.append("")

    counts: dict[str, int] = {}
    for p in props:
        key = p.verdict.value if p.verdict else p.status.value
        counts[key] = counts.get(key, 0) + 1
    lines.append("## Summary")
    lines.append("")
    lines.append(" | ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    lines.append("")

    # Deterministic vocabulary normalizations, surfaced so every rewrite of the
    # emitted FOL is auditable (loyalty-to-text: the merge is a lens, not an
    # silent alteration of what the author said).
    if report.predicate_merges or report.guard_strips:
        lines.append("## Vocabulary normalization (deterministic, audited)")
        lines.append("")
        for m in report.predicate_merges:
            swap = " (arguments swapped: inverse phrasing)" if m.get("args_swapped") else ""
            lines.append(f"- treated `{m['from']}` and `{m['to']}` as the same relation; "
                         f"kept `{m['to']}`{swap}: {m.get('reason', '')}")
        for g in report.guard_strips:
            lines.append(f"- {g['id']}: stripped dangling type-guard `{g['guard']}` "
                         f"(`{g['from']}` → `{g['to']}`): {g.get('reason', '')}")
        lines.append("")

    lines.append("## Statements")
    lines.append("")
    lines.append("| id | type | gate | verdict | conf | statement | FOL |")
    lines.append("|---|---|---|---|---|---|---|")
    for p in props:
        verdict = p.verdict.value if p.verdict else "-"
        fol = f"`{p.fol}`" if p.fol else "-"
        lines.append(
            f"| {p.id} | {p.type.value} | {p.status.value} | {verdict} | "
            f"{p.confidence:.2f} | {p.decontextualized} | {fol} |"
        )
    lines.append("")

    contradictions = [p for p in props if p.verdict == Verdict.CONTRADICTS]
    if contradictions:
        lines.append("## Minimal inconsistent sets")
        lines.append("")
        lines.append(
            "Each set below is a minimal collection of statements that cannot all be "
            "true at once. At least one member of each set must be abandoned. The "
            "system identifies the conflict; it does not determine which member to reject."
        )
        lines.append("")
        seen: set[frozenset] = set()
        for p in contradictions:
            members = sorted({p.id, *p.conflict})
            key = frozenset(members)
            if key in seen:
                continue
            seen.add(key)
            bridges = [m for m in members if by_id.get(m) and by_id[m].type == StatementType.BRIDGE]
            kind = "bridged" if bridges else "self-contained"
            lines.append(f"### Inconsistent set {{{', '.join(members)}}} ({kind})")
            lines.append("")
            for mid in members:
                mp = by_id.get(mid)
                if not mp:
                    continue
                span = f" (chars {mp.span.start}-{mp.span.end})" if mp.span else ""
                tag = " [background premise, not stated in the text]" if mp.type == StatementType.BRIDGE else ""
                lines.append(f"- {mid}{tag}: \u201c{mp.original_text}\u201d{span} -> `{mp.fol}`")
            if bridges:
                lines.append(
                    f"- Note: this inconsistency is only detectable if you also accept "
                    f"{', '.join(bridges)}. Without that background premise, the author's "
                    f"own statements remain mutually consistent."
                )
            lines.append("")

    entailed = [p for p in props if p.verdict == Verdict.ENTAILED]
    if entailed:
        lines.append("## Entailed claims (formally proven from the author's axioms)")
        lines.append("")
        for p in entailed:
            support = ", ".join(p.support) if p.support else "(empty support)"
            lines.append(f"- {p.id}: \u201c{p.decontextualized}\u201d follows from: {support}")
        lines.append("")

    refuted = [p for p in props if p.verdict == Verdict.REFUTED]
    if refuted:
        lines.append("## Refuted suppositions (reductio ad absurdum)")
        lines.append("")
        lines.append(
            "The author introduces these suppositions in order to refute them: each "
            "contradicts the established theory, so its negation is proven. This is a "
            "valid argumentative move (proof by contradiction), NOT an inconsistency "
            "in the author's own asserted claims."
        )
        lines.append("")
        for p in refuted:
            conflict = ", ".join(p.conflict) if p.conflict else "the established theory"
            lines.append(
                f"- {p.id}: “{p.original_text}” contradicts {conflict} "
                f"→ its negation is thereby proven"
            )
        lines.append("")

    not_entailed = [p for p in props if p.verdict == Verdict.NOT_ENTAILED]
    if not_entailed:
        lines.append("## Unverifiable claims (consistent with the axioms but not provable from them)")
        lines.append("")
        lines.append(
            "These claims do not contradict anything, but the author's stated premises "
            "are insufficient to prove them. The argument may rely on unstated assumptions. "
            "This is an incomplete argument, not an inconsistency."
        )
        lines.append("")
        for p in not_entailed:
            lines.append(f"- {p.id}: \u201c{p.decontextualized}\u201d")
        lines.append("")

    unknown = [p for p in props if p.verdict == Verdict.UNKNOWN]
    if unknown:
        lines.append("## Unknown (solver could not decide within limits)")
        lines.append("")
        for p in unknown:
            lines.append(f"- {p.id}: \u201c{p.decontextualized}\u201d")
        lines.append("")

    flagged = [p for p in props if p.status in (GateOutcome.AMBIGUOUS, GateOutcome.QUARANTINED)]
    if flagged:
        lines.append("## Excluded from the axiom set (nothing is dropped silently)")
        lines.append("")
        for p in flagged:
            lines.append(f"- {p.id} [{p.status.value}]: \u201c{p.original_text}\u201d :: {p.gate_reason}")
        lines.append("")

    shapes: dict[str, int] = {}
    for p in props:
        if p.quarantine_shape:
            shapes[p.quarantine_shape] = shapes.get(p.quarantine_shape, 0) + 1
    if shapes:
        total = sum(shapes.values())
        lines.append("## Outside-fragment shapes (what relations/logic would buy us)")
        lines.append("")
        lines.append(
            "Heuristic buckets for statements that fell outside the unary FOL fragment. "
            "Aggregate proportions, not individual labels, indicate which extension is "
            "highest-leverage next: `relational-ground` favors EPR; `relational-role(\u2200\u2203)` "
            "favors description logic; `modal-deontic`/`causal`/`comparative-numeric` need "
            "their own logics. Total outside-fragment: "
            f"{total}."
        )
        lines.append("")
        for k, v in sorted(shapes.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- {k}: {v} ({100 * v / total:.0f}%)")
        lines.append("")

    lines.append("## Surface screener (lexical placeholder for the NLI path)")
    lines.append("")
    if report.screener:
        for f in report.screener:
            lines.append(f"- {f['a']} vs {f['b']}: {f['signal']} (overlap {f['jaccard']})")
        lines.append("")
        lines.append(
            "Screener flags are cheap surface signals, not verdicts; the symbolic "
            "verdicts above are authoritative. Multi-hop inconsistencies are invisible "
            "to the screener by design."
        )
    else:
        lines.append("No surface-level conflicts flagged.")
    lines.append("")

    lines.append("## Theory tree")
    lines.append("")
    lines.append("Diagram files: `graph.svg` (open in a browser or PyCharm), `graph.dot` (Graphviz), `graph.png` (if Graphviz is installed).")
    lines.append("")
    lines.append("```text")
    lines.append(build_tree_text(report))
    lines.append("```")
    lines.append("")

    lines.append("## Cluster diagnostics")
    lines.append("")
    for c in report.clusters:
        cons = "n/a" if c.axioms_consistent is None else str(c.axioms_consistent)
        flags = []
        if c.hit_timeout:
            flags.append("TIMEOUT/UNKNOWN")
        flag_s = f" [{', '.join(flags)}]" if flags else ""
        note = f" :: {c.note}" if c.note else ""
        lines.append(
            f"- cluster {c.cluster_id}: {c.n_statements} statements, consistent: {cons}, "
            f"solver {c.solver_ms:.1f} ms{flag_s}{note}"
        )
        if c.axiom_conflict:
            lines.append(f"  - minimal inconsistent set: {', '.join(c.axiom_conflict)}")
    if not report.clusters:
        lines.append("- solver skipped (effort 0)")
    lines.append("")

    lines.append("## Timing")
    lines.append("")
    lines.append("| stage | seconds |")
    lines.append("|---|---|")
    for r in report.timing:
        lines.append(f"| {r['stage']} | {r['seconds']:.4f} |")
    lines.append("")

    lines.append("## Shared vocabulary")
    lines.append("")
    lines.append(f"Predicates: {', '.join(report.vocabulary_predicates) or '(none)'}")
    lines.append("")
    lines.append(f"Constants: {', '.join(report.vocabulary_constants) or '(none)'}")
    lines.append("")
    return "\n".join(lines)
