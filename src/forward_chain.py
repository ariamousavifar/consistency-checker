"""Forward-chaining refutation prover -- reconstructs the DERIVATION that leads
to a contradiction, so an inconsistent argument can still be shown as a tree
(two chains of theorems colliding) rather than a flat unsat-core fan.

Why this exists. The Z3 layer in solver.py answers "is this set consistent?" and,
when it is not, returns a minimal unsat CORE -- the smallest set of statements
that cannot all hold. That is correct but structureless: if two long chains of
relational reasoning each derive a fact and those facts are negations of each
other (P and not P), the core is just "these axioms clash," with none of the
intermediate theorems that got there. From an inconsistent set everything is
entailed, so Z3-entailment cannot rebuild the chain either.

A proof, by contrast, is constructive: start from the ground facts, apply the
rules step by step, record WHICH facts and WHICH rule produced each new fact, and
stop the moment a literal and its complement are both derived. The provenance of
those two literals is exactly the two derivation chains. We stay inside the
decidable fragment the rest of the tool targets (EPR: finitely many constants, no
function symbols), so naive saturation terminates.

Supported clause shapes (anything else is ignored -- this is an EXPLAINER layered
on top of the sound Z3 verdict, never the verdict itself):
  - ground fact            P(a, b)          /  not P(a, b)
  - definite rule          forall .. ((B1 and B2 and ..) -> H)   H positive or negated
  - bare universal fact    forall x. (not P(x, x))   (e.g. irreflexivity)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from .fol_parser import KEYWORDS, tokenize


# --------------------------------------------------------------------------- #
#  Literals and clauses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Lit:
    neg: bool
    pred: str
    args: tuple[str, ...]

    def complement(self) -> "Lit":
        return Lit(not self.neg, self.pred, self.args)

    def __str__(self) -> str:
        inner = f"{self.pred}({', '.join(self.args)})"
        return f"not {inner}" if self.neg else inner


@dataclass
class Rule:
    stmt_id: str
    vars: frozenset[str]
    body: tuple[Lit, ...]      # positive atoms over vars/constants
    head: Lit                  # may be negated


@dataclass
class Step:
    """One node of the derivation: `lit` was produced by `stmt_id` (a fact's or
    rule's statement id) from `premises` (the ground literals it consumed)."""
    lit: Lit
    stmt_id: str
    premises: tuple[Lit, ...] = ()


@dataclass
class Refutation:
    left: Lit                       # the two clashing ground literals
    right: Lit
    steps: dict[tuple, Step] = field(default_factory=dict)   # lit-key -> Step

    def key(self, lit: Lit) -> tuple:
        return (lit.neg, lit.pred, lit.args)


# --------------------------------------------------------------------------- #
#  Parsing FOL strings into facts / rules (restricted recognizer)
# --------------------------------------------------------------------------- #
class _Bail(Exception):
    pass


class _Reader:
    """Recursive-descent parser over tokens that builds a small node tree and
    BAILS on anything outside the supported fragment (or, exists, <->). Node
    forms: ('forall',[vars],node) ('impl',l,r) ('and',[nodes]) ('not',node)
    ('atom',pred,(args,))."""

    def __init__(self, toks: list[str]) -> None:
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def nxt(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, tok):
        if self.nxt() != tok:
            raise _Bail

    def parse(self):
        f = self.formula()
        if self.peek() is not None:
            raise _Bail
        return f

    def formula(self):
        left = self.implies()
        if self.peek() == "<->":
            raise _Bail
        return left

    def implies(self):
        left = self.disj()
        if self.peek() == "->":
            self.nxt()
            return ("impl", left, self.implies())
        return left

    def disj(self):
        left = self.conj()
        if self.peek() == "or":
            raise _Bail
        return left

    def conj(self):
        parts = [self.unary()]
        while self.peek() == "and":
            self.nxt()
            parts.append(self.unary())
        return ("and", parts) if len(parts) > 1 else parts[0]

    def unary(self):
        tok = self.peek()
        if tok == "not":
            self.nxt()
            return ("not", self.unary())
        if tok == "exists":
            raise _Bail
        if tok == "forall":
            self.nxt()
            vs = [self.nxt()]
            while self.peek() == ",":
                self.nxt()
                vs.append(self.nxt())
            self.expect(".")
            return ("forall", vs, self.formula())
        if tok == "(":
            self.nxt()
            f = self.formula()
            self.expect(")")
            return f
        name = self.nxt()
        if name is None or name in KEYWORDS or not (name[0].isalpha() or name[0] == "_"):
            raise _Bail
        self.expect("(")
        args = [self._term()]
        while self.peek() == ",":
            self.nxt()
            args.append(self._term())
        self.expect(")")
        return ("atom", name, tuple(args))

    def _term(self):
        name = self.nxt()
        if name is None or name in KEYWORDS or not (name[0].isalpha() or name[0] == "_"):
            raise _Bail
        return name


def _as_lit(node):
    if node[0] == "atom":
        return Lit(False, node[1], node[2])
    if node[0] == "not" and node[1][0] == "atom":
        return Lit(True, node[1][1], node[1][2])
    return None


def _as_body(node):
    atoms = node[1] if node[0] == "and" else [node]
    out: list[Lit] = []
    for a in atoms:
        lit = _as_lit(a)
        if lit is None or lit.neg:
            return None            # negative / non-atomic body atom unsupported
        out.append(lit)
    return out


def _as_facts(node):
    """A ground atom, negated atom, or conjunction of them -> list of literals.
    Lets 'P(a, b) and P(a, c)' (a course requiring two prerequisites) become two
    facts instead of being rejected."""
    atoms = node[1] if node[0] == "and" else [node]
    out: list[Lit] = []
    for a in atoms:
        lit = _as_lit(a)
        if lit is None:
            return None
        out.append(lit)
    return out


def parse_clause(fol: str, stmt_id: str):
    """Return ('fact', Lit) | ('rule', Rule) | None."""
    if not fol:
        return None
    try:
        node = _Reader(tokenize(fol)).parse()
    except (_Bail, Exception):
        return None
    vs: set[str] = set()
    while node[0] == "forall":
        vs |= set(node[1])
        node = node[2]
    if node[0] == "impl":
        body = _as_body(node[1])
        head = _as_lit(node[2])
        if body is None or head is None:
            return None
        return ("rule", Rule(stmt_id, frozenset(vs), tuple(body), head))
    if vs:                          # bare universal fact (e.g. irreflexivity)
        lit = _as_lit(node)
        if lit is None:
            return None
        return ("rule", Rule(stmt_id, frozenset(vs), (), lit))
    facts = _as_facts(node)         # ground atom(s), conjunction allowed
    if facts is None:
        return None
    if len(facts) == 1:
        return ("fact", facts[0])
    return ("facts", facts)


# --------------------------------------------------------------------------- #
#  Saturation
# --------------------------------------------------------------------------- #
def explain(props, max_facts: int = 5000) -> Refutation | None:
    """Forward-chain the accepted props; return a Refutation if a literal and its
    complement are both derived, else None. `props` are objects with `.id`,
    `.fol`, and a `.status`/`.fol` already filtered by the caller."""
    facts: list[tuple[Lit, str]] = []
    rules: list[Rule] = []
    for p in props:
        parsed = parse_clause(getattr(p, "fol", None), p.id)
        if not parsed:
            continue
        kind, payload = parsed
        if kind == "fact":
            facts.append((payload, p.id))
        elif kind == "facts":
            for lit in payload:
                facts.append((lit, p.id))
        else:
            rules.append(payload)

    steps: dict[tuple, Step] = {}
    by_pred: dict[tuple[str, int], list[Lit]] = {}   # positive atoms only
    domain: set[str] = set()

    def key(lit: Lit) -> tuple:
        return (lit.neg, lit.pred, lit.args)

    def add(lit: Lit, stmt_id: str, premises: tuple[Lit, ...]):
        k = key(lit)
        if k in steps:
            return None
        steps[k] = Step(lit, stmt_id, premises)
        for a in lit.args:
            domain.add(a)
        if not lit.neg:
            by_pred.setdefault((lit.pred, len(lit.args)), []).append(lit)
        # contradiction?
        ck = key(lit.complement())
        if ck in steps:
            return (steps[ck].lit, lit)
        return None

    # seed ground facts
    for lit, sid in facts:
        clash = add(lit, sid, ())
        if clash:
            return _refute(clash, steps, key)

    # saturate
    changed = True
    while changed and len(steps) < max_facts:
        changed = False
        for rule in rules:
            for binding, prem in _matches(rule, by_pred, domain):
                head = _ground(rule.head, binding)
                if key(head) in steps:
                    continue
                clash = add(head, rule.stmt_id, prem)
                changed = True
                if clash:
                    return _refute(clash, steps, key)
    return None


def _ground(lit: Lit, binding: dict[str, str]) -> Lit:
    return Lit(lit.neg, lit.pred, tuple(binding.get(a, a) for a in lit.args))


def _matches(rule: Rule, by_pred, domain):
    """Yield (binding, premises) for every way the rule body matches known facts.
    Bare-body rules (e.g. irreflexivity) are instantiated over the domain."""
    if not rule.body:
        vlist = sorted(rule.vars)
        if not vlist or not domain:
            return
        for combo in product(sorted(domain), repeat=len(vlist)):
            yield dict(zip(vlist, combo)), ()
        return

    results: list[tuple[dict[str, str], tuple[Lit, ...]]] = [({}, ())]
    for atom in rule.body:
        nxt: list[tuple[dict[str, str], tuple[Lit, ...]]] = []
        cands = by_pred.get((atom.pred, len(atom.args)), [])
        for binding, prem in results:
            for fact in cands:
                b2 = dict(binding)
                ok = True
                for a, v in zip(atom.args, fact.args):
                    if a in rule.vars:
                        if a in b2 and b2[a] != v:
                            ok = False
                            break
                        b2[a] = v
                    elif a != v:        # constant in body must match
                        ok = False
                        break
                if ok:
                    nxt.append((b2, prem + (fact,)))
        results = nxt
        if not results:
            return
    for binding, prem in results:
        yield binding, prem


def _refute(clash, steps, key) -> Refutation:
    left, right = clash
    return Refutation(left=left, right=right, steps=dict(steps))


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def _node_id(lit: Lit) -> str:
    sign = "n" if lit.neg else "p"
    return "fc_" + sign + "_" + lit.pred + "_" + "_".join(lit.args)


def serialize(ref: Refutation, prune: bool = True) -> dict:
    """JSON-friendly form for the report/renderer. Each derived literal becomes a
    node; FACT literals carry their statement id so the renderer can fuse them
    with the existing statement node instead of duplicating it.

    prune=True (default): keep only the nodes on the provenance paths to the two
    clashing tips -- saturation derives many facts irrelevant to THIS
    contradiction, and dumping them all would bury the two chains. prune=False
    (--full-derivation): keep EVERY derived fact, the complete forward-chaining
    closure."""
    def k(lit: Lit) -> tuple:
        return (lit.neg, lit.pred, lit.args)

    keep: set[tuple] | None = None
    if prune:
        keep = set()

        def walk(key_t: tuple) -> None:
            if key_t in keep:
                return
            keep.add(key_t)
            step = ref.steps.get(key_t)
            if step:
                for p in step.premises:
                    walk(k(p))

        walk(k(ref.left))
        walk(k(ref.right))

    nodes: dict[str, dict] = {}
    for key_t, step in ref.steps.items():
        if keep is not None and key_t not in keep:
            continue
        nodes[_node_id(step.lit)] = {
            "label": str(step.lit),
            "stmt_id": step.stmt_id,         # producing fact-id or rule-id
            "premises": [_node_id(p) for p in step.premises],
            "is_fact": not step.premises,
        }
    return {
        "left": _node_id(ref.left),
        "right": _node_id(ref.right),
        "left_label": str(ref.left),
        "right_label": str(ref.right),
        "nodes": nodes,
        "text": render_text(ref),
    }


def render_text(ref: Refutation, indent: str = "") -> str:
    """An indented proof: the two clashing literals, each expanded to its
    derivation, bottoming out at ground facts."""
    seen: set[tuple] = set()

    def walk(lit: Lit, depth: int) -> list[str]:
        k = (lit.neg, lit.pred, lit.args)
        pad = indent + "  " * depth
        step = ref.steps.get(k)
        if step is None:
            return [f"{pad}{lit}  (?)"]
        if not step.premises:
            return [f"{pad}{lit}  [{step.stmt_id}]"]
        tag = "" if k not in seen else "  (above)"
        seen.add(k)
        out = [f"{pad}{lit}  <-[{step.stmt_id}]{tag}"]
        if not tag:
            for prem in step.premises:
                out += walk(prem, depth + 1)
        return out

    lines = ["CONTRADICTION: derived both", f"  {ref.left}", "and", f"  {ref.right}", ""]
    lines += ["chain for " + str(ref.left) + ":"]
    lines += walk(ref.left, 1)
    lines += ["chain for " + str(ref.right) + ":"]
    lines += walk(ref.right, 1)
    return "\n".join(lines)
