"""Report generation: human-readable Markdown plus machine-readable JSON."""
from __future__ import annotations

from .schema import GateOutcome, RunReport, Verdict
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
    lines.append(f"Source: `{report.source_file}` | mode: {report.mode}")
    lines.append("")

    counts: dict[str, int] = {}
    for p in props:
        key = p.verdict.value if p.verdict else p.status.value
        counts[key] = counts.get(key, 0) + 1
    lines.append("## Summary")
    lines.append("")
    lines.append(" | ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
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
        lines.append("## Contradictions found")
        lines.append("")
        for p in contradictions:
            span = f" (chars {p.span.start}-{p.span.end})" if p.span else ""
            lines.append(f"### {p.id}: \u201c{p.original_text}\u201d{span}")
            lines.append("")
            lines.append(f"- Formalized as: `{p.fol}` ({_safe_verbalize(p.fol)})")
            lines.append("- Minimal conflicting set:")
            for cid in p.conflict:
                cp = by_id.get(cid)
                if cp:
                    cspan = f" (chars {cp.span.start}-{cp.span.end})" if cp.span else ""
                    lines.append(f"  - {cid}: \u201c{cp.original_text}\u201d{cspan} -> `{cp.fol}`")
            lines.append(
                "- Reading: these statements cannot all be true at once; "
                "the author has violated their own stated premises."
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

    not_entailed = [p for p in props if p.verdict == Verdict.NOT_ENTAILED]
    if not_entailed:
        lines.append("## Unverifiable claims (consistent with the axioms but not provable from them)")
        lines.append("")
        for p in not_entailed:
            lines.append(f"- {p.id}: \u201c{p.decontextualized}\u201d")
        lines.append("")

    flagged = [p for p in props if p.status in (GateOutcome.AMBIGUOUS, GateOutcome.QUARANTINED)]
    if flagged:
        lines.append("## Excluded from the axiom set (nothing is dropped silently)")
        lines.append("")
        for p in flagged:
            lines.append(f"- {p.id} [{p.status.value}]: \u201c{p.original_text}\u201d :: {p.gate_reason}")
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
        note = f" :: {c.note}" if c.note else ""
        lines.append(f"- cluster {c.cluster_id}: {len(c.statement_ids)} statements, axioms consistent: {cons}{note}")
        if c.axiom_conflict:
            lines.append(f"  - minimal inconsistent axiom set: {', '.join(c.axiom_conflict)}")
    lines.append("")

    lines.append("## Shared vocabulary")
    lines.append("")
    lines.append(f"Predicates: {', '.join(report.vocabulary_predicates) or '(none)'}")
    lines.append("")
    lines.append(f"Constants: {', '.join(report.vocabulary_constants) or '(none)'}")
    lines.append("")
    return "\n".join(lines)
