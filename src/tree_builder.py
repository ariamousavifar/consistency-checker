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
    "bridge": ("#EEEDFE", "#534AB7"),
    "entailed": ("#d6edcb", "#3b6d11"),
    "not_entailed": ("#faeeda", "#854f0b"),
    "contradicts": ("#f7c1c1", "#a32d2d"),
    "unknown": ("#e8e8e4", "#5f5e5a"),
    "error": ("#e8e8e4", "#5f5e5a"),
    "excluded": ("#f1efe8", "#888780"),
}

_MARK = {
    "axiom": "AX",
    "bridge": "BR",
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
    if p.type == StatementType.BRIDGE:
        return "bridge"
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
        bridges = [p for p in members if p.type == StatementType.BRIDGE]
        claims = [p for p in members if p.type not in (StatementType.AXIOM, StatementType.BRIDGE)]

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
                    kids.append((f"INCOMPATIBLE WITH {leaf(cp)}", []))
            claim_nodes.append((leaf(p), kids))

        root_label = f"theory cluster {c.cluster_id}  [axioms consistent: {cons}]"
        children = [
            (f"axioms ({len(axioms)})", [(leaf(p), []) for p in axioms]),
        ]
        if bridges:
            children.append((f"bridge premises ({len(bridges)})", [(leaf(p), []) for p in bridges]))
        children.append((f"claims ({len(claims)})", claim_nodes))
        if c.axiom_conflict:
            children.insert(0, ("MINIMAL INCONSISTENT SET",
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

    legend = "legend: AX axiom | BR bridge premise | OK entailed | ?? not entailed (unprovable) | XX member of an inconsistent set | ~? unknown | -- excluded"
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
        for did in p.depends_on:
            if did in by_id:
                out.append(f'  {did} -> {p.id} [color="#185fa5", style=dotted, arrowsize=0.6];')

    # Minimal inconsistent sets: draw ONE hub per set, with every member joined
    # to the hub. This shows the set is JOINTLY unsatisfiable, not that any pair
    # individually contradicts (the old pairwise edges drew a misleading triangle
    # implying every member conflicts with every other).
    seen_sets: set[frozenset] = set()
    hub_i = 0
    for p in report.propositions:
        if p.verdict == Verdict.CONTRADICTS:
            members = frozenset({p.id, *p.conflict})
            if not members or members in seen_sets:
                continue
            seen_sets.add(members)
            hub = f"conflict_hub_{hub_i}"
            hub_i += 1
            out.append(
                f'  {hub} [label="minimal\\ninconsistent set", shape=octagon, '
                f'fillcolor="#f6dcdc", color="#a32d2d", fontsize=9, style="filled"];'
            )
            for mid in members:
                if mid in by_id:
                    out.append(
                        f'  {mid} -> {hub} [color="#a32d2d", style=dashed, '
                        f'arrowsize=0.6, dir=none];'
                    )
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
        axioms = [p for p in members if p.type in (StatementType.AXIOM, StatementType.BRIDGE)]
        claims = [p for p in members if p.type not in (StatementType.AXIOM, StatementType.BRIDGE)]
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

    # Minimal inconsistent sets as translucent bands, NOT pairwise edges. A band
    # encloses all members of one jointly-unsatisfiable set, so it reads as "these
    # together cannot all hold" rather than "every pair contradicts".
    bands: list[str] = []
    seen_sets: set[frozenset] = set()
    for p in report.propositions:
        if p.verdict != Verdict.CONTRADICTS:
            continue
        members = frozenset({p.id, *p.conflict})
        if not members or members in seen_sets:
            continue
        seen_sets.add(members)
        pts = [pos[m] for m in members if m in pos]
        if not pts:
            continue
        minx = min(x for x, _ in pts) - 8
        miny = min(y for _, y in pts) - 8
        maxx = max(x for x, _ in pts) + _NODE_W + 8
        maxy = max(y for _, y in pts) + _NODE_H + 8
        bands.append(
            f'<rect x="{minx}" y="{miny}" width="{maxx - minx}" height="{maxy - miny}" '
            f'rx="10" fill="#a32d2d" fill-opacity="0.06" stroke="#a32d2d" '
            f'stroke-width="1.4" stroke-dasharray="6,4"/>'
            f'<text x="{minx + 6}" y="{miny + 14}" font-family="Helvetica,Arial" font-size="10" '
            f'fill="#a32d2d">minimal inconsistent set</text>'
        )

    nodes = [_node_svg(p, *pos[p.id]) for p in report.propositions if p.id in pos]
    legend_y = height - 14
    legend = (
        f'<text x="{width / 2}" y="{legend_y}" text-anchor="middle" font-family="Helvetica,Arial" font-size="11" '
        f'fill="#5f5e5a">blue axiom | green entailed | amber not entailed | red contradicts | '
        f'dashed gray excluded | red dashed band = minimal inconsistent set | gray edge = proof support</text>'
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
        + "".join(bands) + "".join(edges) + "".join(nodes) + legend
        + "</svg>"
    )
