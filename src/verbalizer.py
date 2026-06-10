"""Deterministic FOL -> stilted English verbalizer.

Used in two places: the fidelity check (compare the verbalization against the
source proposition) and contradiction reports (human-readable formulas). The
output is deliberately stiff but unambiguous.
"""
from __future__ import annotations

from .fol_parser import KEYWORDS, FOLParseError, tokenize
from .vocabulary import words_of


def _parse_ast(tokens: list[str]):
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def nxt():
        t = peek()
        pos[0] += 1
        return t

    def expect(t):
        got = nxt()
        if got != t:
            raise FOLParseError(f"expected {t!r}, got {got!r}")

    def formula():
        left = implies()
        while peek() == "<->":
            nxt()
            left = ("iff", left, implies())
        return left

    def implies():
        left = disj()
        if peek() == "->":
            nxt()
            return ("implies", left, implies())
        return left

    def disj():
        left = conj()
        while peek() == "or":
            nxt()
            left = ("or", left, conj())
        return left

    def conj():
        left = unary()
        while peek() == "and":
            nxt()
            left = ("and", left, unary())
        return left

    def unary():
        t = peek()
        if t == "not":
            nxt()
            return ("not", unary())
        if t in ("forall", "exists"):
            quant = nxt()
            vs = [nxt()]
            while peek() == ",":
                nxt()
                vs.append(nxt())
            expect(".")
            return (quant, vs, formula())
        if t == "(":
            nxt()
            f = formula()
            expect(")")
            return f
        name = nxt()
        if name is None or name in KEYWORDS:
            raise FOLParseError(f"expected atom, got {name!r}")
        expect("(")
        args = []
        if peek() != ")":
            args.append(nxt())
            while peek() == ",":
                nxt()
                args.append(nxt())
        expect(")")
        return ("atom", name, args)

    ast = formula()
    if peek() is not None:
        raise FOLParseError("trailing tokens")
    return ast


def _pred_phrase(name: str) -> str:
    return " ".join(words_of(name))


def _verbalize(node) -> str:
    kind = node[0]
    if kind == "atom":
        _, name, args = node
        phrase = _pred_phrase(name)
        if len(args) == 1:
            return f"{args[0]} is {phrase}"
        return f"{phrase} holds for ({', '.join(args)})"
    if kind == "not":
        return f"it is not the case that {_verbalize(node[1])}"
    if kind == "and":
        return f"{_verbalize(node[1])} and {_verbalize(node[2])}"
    if kind == "or":
        return f"{_verbalize(node[1])} or {_verbalize(node[2])}"
    if kind == "implies":
        return f"if {_verbalize(node[1])}, then {_verbalize(node[2])}"
    if kind == "iff":
        return f"{_verbalize(node[1])} if and only if {_verbalize(node[2])}"
    if kind == "forall":
        vs = " and ".join(node[1])
        return f"for every {vs}: {_verbalize(node[2])}"
    if kind == "exists":
        vs = " and ".join(node[1])
        return f"there is some {vs} such that {_verbalize(node[2])}"
    raise FOLParseError(f"unknown node {kind}")


def verbalize(fol: str) -> str:
    return _verbalize(_parse_ast(tokenize(fol)))
