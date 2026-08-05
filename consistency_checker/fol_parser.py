"""First-order-logic string -> Z3 expression.

Grammar (informal):
    formula  := iff
    iff      := implies ("<->" implies)*
    implies  := disj ("->" implies)?          (right associative)
    disj     := conj ("or" conj)*
    conj     := unary ("and" unary)*
    unary    := "not" unary
              | ("forall"|"exists") var ("," var)* "." formula
              | "(" formula ")"
              | atom
    atom     := Identifier "(" term ("," term)* ")"
    term     := bound variable | constant

Quantifier bodies are parsed greedily; always parenthesise them, e.g.
    forall x. (Human(x) -> Mortal(x))
"""
from __future__ import annotations

import z3

KEYWORDS = {"forall", "exists", "and", "or", "not"}


class FOLParseError(Exception):
    pass


def tokenize(s: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if s.startswith("<->", i):
            tokens.append("<->")
            i += 3
            continue
        if s.startswith("->", i):
            tokens.append("->")
            i += 2
            continue
        if c in "(),.":
            tokens.append(c)
            i += 1
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < len(s) and (s[j].isalnum() or s[j] == "_"):
                j += 1
            tokens.append(s[i:j])
            i = j
            continue
        raise FOLParseError(f"unexpected character {c!r} in FOL string")
    return tokens


class Env:
    """Shared Z3 declarations: one sort, predicates by (name, arity), constants by name."""

    def __init__(self) -> None:
        self.sort = z3.DeclareSort("Object")
        self.preds: dict[tuple[str, int], z3.FuncDeclRef] = {}
        self.consts: dict[str, z3.ExprRef] = {}

    def pred(self, name: str, arity: int) -> z3.FuncDeclRef:
        key = (name, arity)
        if key not in self.preds:
            self.preds[key] = z3.Function(name, *([self.sort] * arity), z3.BoolSort())
        return self.preds[key]

    def const(self, name: str) -> z3.ExprRef:
        if name not in self.consts:
            self.consts[name] = z3.Const(name, self.sort)
        return self.consts[name]


class _Parser:
    def __init__(self, tokens: list[str], env: Env) -> None:
        self.t = tokens
        self.i = 0
        self.env = env

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self) -> str | None:
        tok = self.peek()
        self.i += 1
        return tok

    def expect(self, tok: str) -> None:
        got = self.next()
        if got != tok:
            raise FOLParseError(f"expected {tok!r}, got {got!r}")

    def parse(self) -> z3.ExprRef:
        f = self.formula({})
        if self.peek() is not None:
            raise FOLParseError(f"trailing tokens starting at {self.peek()!r}")
        return f

    def formula(self, bound: dict[str, z3.ExprRef]) -> z3.ExprRef:
        left = self.implies(bound)
        while self.peek() == "<->":
            self.next()
            right = self.implies(bound)
            left = left == right  # boolean equality is iff in Z3
        return left

    def implies(self, bound):
        left = self.disj(bound)
        if self.peek() == "->":
            self.next()
            return z3.Implies(left, self.implies(bound))
        return left

    def disj(self, bound):
        left = self.conj(bound)
        while self.peek() == "or":
            self.next()
            left = z3.Or(left, self.conj(bound))
        return left

    def conj(self, bound):
        left = self.unary(bound)
        while self.peek() == "and":
            self.next()
            left = z3.And(left, self.unary(bound))
        return left

    def unary(self, bound):
        tok = self.peek()
        if tok == "not":
            self.next()
            return z3.Not(self.unary(bound))
        if tok in ("forall", "exists"):
            quant = self.next()
            var_names = [self.next()]
            while self.peek() == ",":
                self.next()
                var_names.append(self.next())
            self.expect(".")
            new_bound = dict(bound)
            zvars = []
            for v in var_names:
                if v is None or not v[0].isalpha():
                    raise FOLParseError("bad quantified variable name")
                zv = z3.Const(v, self.env.sort)
                new_bound[v] = zv
                zvars.append(zv)
            body = self.formula(new_bound)
            return z3.ForAll(zvars, body) if quant == "forall" else z3.Exists(zvars, body)
        if tok == "(":
            self.next()
            f = self.formula(bound)
            self.expect(")")
            return f
        # atom
        name = self.next()
        if name is None or not (name[0].isalpha() or name[0] == "_") or name in KEYWORDS:
            raise FOLParseError(f"expected atom, got {name!r}")
        if self.peek() != "(":
            raise FOLParseError(f"bare identifier {name!r}; predicates need arguments")
        self.next()
        args = []
        if self.peek() != ")":
            args.append(self.term(bound))
            while self.peek() == ",":
                self.next()
                args.append(self.term(bound))
        self.expect(")")
        return self.env.pred(name, len(args))(*args)

    def term(self, bound):
        name = self.next()
        if name is None or name in KEYWORDS or not (name[0].isalpha() or name[0] == "_"):
            raise FOLParseError(f"bad term {name!r}")
        if name in bound:
            return bound[name]
        return self.env.const(name)


def parse_fol(s: str, env: Env | None = None) -> tuple[z3.ExprRef, Env]:
    env = env or Env()
    return _Parser(tokenize(s), env).parse(), env


def check_equivalence(fol_a: str, fol_b: str, timeout_ms: int = 3000) -> str:
    """Prove or refute logical equivalence of two FOL strings.

    Returns "equivalent", "divergent", or "unknown".
    Both strings are parsed into a single shared Env so identical predicate
    names refer to identical Z3 declarations.
    """
    env = Env()
    fa, _ = parse_fol(fol_a, env)
    fb, _ = parse_fol(fol_b, env)
    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    solver.add(z3.Not(fa == fb))
    res = solver.check()
    if res == z3.unsat:
        return "equivalent"
    if res == z3.sat:
        return "divergent"
    return "unknown"
