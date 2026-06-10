"""Theory visualization.

Four renderings of the same structure (axioms, claims, entailment support,
contradiction conflicts, exclusions), color coded by verdict:

  blue   = axiom
  green  = entailed claim
  amber  = not_entailed claim (consistent but unprovable)
  red    = contradicts
  gray   = unknown / error
  dashed = quarantined or ambiguous (excluded from the theory)

1. build_tree_text  -> ASCII tree for console output and report.md
2. build_dot        -> Graphviz source (out/graph.dot); render anywhere
3. build_svg        -> standalone SVG (out/graph.svg), zero dependencies,
                       opens in any browser or in PyCharm's preview
4. render_png       -> out/graph.png via the local `dot` binary, if installed
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .schema import GateOutcome, Proposition, RunReport, StatementType, Verdict

_FILL = {
    "axiom": ("#cfe0f5", "#185fa5"),
    "entailed": ("#d6edcb", "#3b6d11"),
    "not_entailed": ("#faeeda", "#854f0b"),
    "contradicts": ("#f7c1c1", "#a32d2d"),
    "unknown": ("#e8e8e4", "#5f5e5a"),
    "error": ("#e8e8e4", "#5f5e5a"),
    "excluded": ("#f1efe8", "#888780"),
}

_MARK = {
    "axiom": "AX",
    "entailed": "OK",
    "not_entailed": "??",
    "contradicts": "XX",
    "unknown": "~?",
    "error": "ER",
    "excluded": "--",
}


def _kind(p: Proposition) -> str:
    if p.status in (GateOutcome.QUARANTINED, GateOutcome.AMBIGUOUS):
        return "excluded"
    if p.type == StatementType.AXIOM:
        return "axiom"
    if p.verdict is None:
        return "unknown"
    return p.verdict.value


def _trunc(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


# ---------------------------------------------------------------- ASCII tree

def _render_tree(label: str, children: list, lines: list[str], prefix: str = "", is_last: bool = True,
                 is_root: bool = False) -> None:
    if is_root:
        lines.append(label)
        child_prefix = ""
    else:
        connector = "`-- " if is_last else "|-- "
        lines.append(prefix + connector + label)
        child_prefix = prefix + ("    " if is_last else "|   ")
    for i, child in enumerate(children):
        clabel, cchildren = child
        _render_tree(clabel, cchildren, lines, child_prefix, i == len(children) - 1)


def build_tree_text(report: RunReport, width: int = 56) -> str:
    by_id = {p.id: p for p in report.propositions}
    lines: list[str] = []

    def leaf(p: Proposition) -> str:
        return f"[{p.id}] {_MARK[_kind(p)]} {_trunc(p.decontextualized, width)}"

    for c in report.clusters:
        cons = "UNKNOWN" if c.axioms_consistent is None else ("YES" if c.axioms_consistent else "NO")
        members = [by_id[i] for i in c.statement_ids if i in by_id]
        axioms = [p for p in members if p.type == StatementType.AXIOM]
        claims = [p for p in members if p.type != StatementType.AXIOM]

        claim_nodes = []
        for p in claims:
            kids = []
            for sid in p.support:
                sp = by_id.get(sid)
                if sp:
                    kids.append((f"proved from {leaf(sp)}", []))
            for cid in p.conflict:
                cp = by_id.get(cid)
                if cp:
                    kids.append((f"CONFLICTS WITH {leaf(cp)}", []))
            claim_nodes.append((leaf(p), kids))

        root_label = f"theory cluster {c.cluster_id}  [axioms consistent: {cons}]"
        children = [
            (f"axioms ({len(axioms)})", [(leaf(p), []) for p in axioms]),
            (f"claims ({len(claims)})", claim_nodes),
        ]
        if c.axiom_conflict:
            children.insert(0, ("MINIMAL INCONSISTENT AXIOM SET",
                                [(leaf(by_id[i]), []) for i in c.axiom_conflict if i in by_id]))
        _render_tree(root_label, children, lines, is_root=True)
        lines.append("")

    excluded = [p for p in report.propositions if _kind(p) == "excluded"]
    if excluded:
        _render_tree(
            f"excluded from theory ({len(excluded)})",
            [(f"[{p.id}] {p.status.value}: {_trunc(p.decontextualized, width)} :: {_trunc(p.gate_reason, 60)}", [])
             for p in excluded],
            lines, is_root=True,
        )
        lines.append("")

    legend = "legend: AX axiom | OK entailed | ?? not entailed (unprovable) | XX contradicts | ~? unknown | -- excluded"
    lines.append(legend)
    return "\n".join(lines)


# ---------------------------------------------------------------- Graphviz

def build_dot(report: RunReport) -> str:
    by_id = {p.id: p for p in report.propositions}
    out = ["digraph theory {", '  rankdir=TB;', '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];']
    for c in report.clusters:
        out.append(f"  subgraph cluster_{c.cluster_id} {{")
        cons = "?" if c.axioms_consistent is None else ("consistent" if c.axioms_consistent else "INCONSISTENT")
        out.append(f'    label="cluster {c.cluster_id} ({cons})"; color="#b4b2a9";')
        for sid in c.statement_ids:
            p = by_id[sid]
            fill, border = _FILL[_kind(p)]
            label = f"{p.id} [{_kind(p)}]\\n{_trunc(p.decontextualized, 40)}"
            out.append(f'    {p.id} [label="{label}", fillcolor="{fill}", color="{border}"];')
        out.append("  }")
    excluded = [p for p in report.propositions if _kind(p) == "excluded"]
    for p in excluded:
        fill, border = _FILL["excluded"]
        label = f"{p.id} [{p.status.value}]\\n{_trunc(p.decontextualized, 40)}"
        out.append(f'  {p.id} [label="{label}", fillcolor="{fill}", color="{border}", style="rounded,filled,dashed"];')
    for p in report.propositions:
        for sid in p.support:
            out.append(f'  {sid} -> {p.id} [color="#5f5e5a", arrowsize=0.7];')
        for cid in p.conflict:
            out.append(f'  {p.id} -> {cid} [color="#a32d2d", style=dashed, arrowsize=0.7, label="conflict", fontcolor="#a32d2d", fontsize=9];')
        for did in p.depends_on:
            if did in by_id:
                out.append(f'  {did} -> {p.id} [color="#185fa5", style=dotted, arrowsize=0.6];')
    out.append("}")
    return "\n".join(out)


def render_png(dot_source: str, png_path: Path) -> bool:
    """Render PNG via the local Graphviz `dot` binary; returns False if unavailable."""
    dot_bin = shutil.which("dot")
    if not dot_bin:
        return False
    try:
        subprocess.run([dot_bin, "-Tpng", "-o", str(png_path)], input=dot_source.encode(),
                       check=True, timeout=30, capture_output=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- Standalone SVG

_NODE_W, _NODE_H, _GAP_X, _GAP_Y = 230, 52, 24, 90


def _node_svg(p: Proposition, x: int, y: int) -> str:
    kind = _kind(p)
    fill, border = _FILL[kind]
    dash = ' stroke-dasharray="5,4"' if kind == "excluded" else ""
    tag = kind if p.type != StatementType.AXIOM or kind == "excluded" else "axiom"
    line1 = f"{p.id}  [{tag}]"
    line2 = _trunc(p.decontextualized, 36)
    return (
        f'<rect x="{x}" y="{y}" width="{_NODE_W}" height="{_NODE_H}" rx="8" fill="{fill}" '
        f'stroke="{border}" stroke-width="1.2"{dash}/>'
        f'<text x="{x + _NODE_W / 2}" y="{y + 20}" text-anchor="middle" font-family="Helvetica,Arial" '
        f'font-size="12" font-weight="bold" fill="{border}">{line1}</text>'
        f'<text x="{x + _NODE_W / 2}" y="{y + 38}" text-anchor="middle" font-family="Helvetica,Arial" '
        f'font-size="11" fill="{border}">{_escape(line2)}</text>'
    )


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_svg(report: RunReport) -> str:
    by_id = {p.id: p for p in report.propositions}
    pos: dict[str, tuple[int, int]] = {}
    rows: list[list[Proposition]] = []

    for c in report.clusters:
        members = [by_id[i] for i in c.statement_ids if i in by_id]
        axioms = [p for p in members if p.type == StatementType.AXIOM]
        claims = [p for p in members if p.type != StatementType.AXIOM]
        if axioms:
            rows.append(axioms)
        if claims:
            rows.append(claims)
    excluded = [p for p in report.propositions if _kind(p) == "excluded"]
    if excluded:
        rows.append(excluded)

    max_cols = max((len(r) for r in rows), default=1)
    width = max_cols * (_NODE_W + _GAP_X) + _GAP_X + 16
    height = len(rows) * (_NODE_H + _GAP_Y) + 60

    for ri, row in enumerate(rows):
        row_w = len(row) * (_NODE_W + _GAP_X) - _GAP_X
        x0 = (width - row_w) // 2
        y = 30 + ri * (_NODE_H + _GAP_Y)
        for ci, p in enumerate(row):
            pos[p.id] = (x0 + ci * (_NODE_W + _GAP_X), y)

    edges: list[str] = []
    for p in report.propositions:
        if p.id not in pos:
            continue
        px, py = pos[p.id]
        for sid in p.support:
            if sid in pos:
                sx, sy = pos[sid]
                edges.append(
                    f'<line x1="{sx + _NODE_W / 2}" y1="{sy + _NODE_H}" x2="{px + _NODE_W / 2}" y2="{py}" '
                    f'stroke="#5f5e5a" stroke-width="1" marker-end="url(#arr)"/>'
                )
        for cid in p.conflict:
            if cid in pos:
                cx, cy = pos[cid]
                edges.append(
                    f'<line x1="{px + _NODE_W / 2}" y1="{py}" x2="{cx + _NODE_W / 2}" y2="{cy + _NODE_H}" '
                    f'stroke="#a32d2d" stroke-width="1.6" stroke-dasharray="6,4" marker-end="url(#arrred)"/>'
                )

    nodes = [_node_svg(p, *pos[p.id]) for p in report.propositions if p.id in pos]
    legend_y = height - 14
    legend = (
        f'<text x="{width / 2}" y="{legend_y}" text-anchor="middle" font-family="Helvetica,Arial" font-size="11" '
        f'fill="#5f5e5a">blue axiom | green entailed | amber not entailed | red contradicts | '
        f'dashed gray excluded | red dashed edge = minimal conflict | gray edge = proof support</text>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<defs>'
        '<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M1 1L9 5L1 9" fill="none" stroke="#5f5e5a" stroke-width="1.4"/></marker>'
        '<marker id="arrred" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M1 1L9 5L1 9" fill="none" stroke="#a32d2d" stroke-width="1.4"/></marker>'
        '</defs>'
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
        + "".join(edges) + "".join(nodes) + legend
        + "</svg>"
    )
