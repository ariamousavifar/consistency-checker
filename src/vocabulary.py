"""Vocabulary alignment (architecture stage 4).

Predicates only contradict inside the solver if they share symbols, so every
FOL string passes through this registry before the gate's equivalence check
and before entering the store. v0.3 components:

- Canonicalizer: deterministic case/plural normalization onto first-seen
  canonical forms (handles Humans/Human, taxes/tax, cities/city).
- Negation map: if a new predicate is the negative-prefix form of a known one
  (Immortal vs Mortal, Unjust vs Just, NonHuman vs Human), it is rewritten as
  `not P(x)` instead of becoming a fresh opaque symbol. This fixes the live-run
  failure where `Immortal(socrates)` made a real contradiction invisible to Z3.
  Direction matters: the base form must be registered first (premises usually
  precede the claims that negate them); the reverse direction is documented as
  a limitation.
- Bridge axioms live in the pipeline (user-supplied, tagged), not here: the
  registry never silently imports semantics, per the loyalty-to-text decision.
"""
from __future__ import annotations

import re

from .fol_parser import KEYWORDS, tokenize
from .lemmatizer import lemma

_IDENT = re.compile(r"[A-Za-z_]\w*$")
_STOPWORDS = {"a", "an", "the"}
NEG_PREFIXES = ("non", "un", "im", "ir", "dis", "in")

# First-person self-reference constants a first-person document/bridge may use
# for the same single author. Merged to one entity by finalize_self_reference_
# aliases (opt-in; single-author scope). Lowercased to match constant keys.
_SELF_REF = frozenset({
    "i", "me", "myself", "author", "speaker", "narrator", "writer",
    "presenter", "oneself", "we", "us",
})


def _plural_strip(word: str) -> str:
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words_of(name: str) -> list[str]:
    """Split CamelCase / snake_case into lowercase words."""
    parts = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name)
    return [p.lower() for p in parts]


# Semantically-light head nouns that frequently appear as a trailing classifier
# ("prime number" = "prime", "industrious creature" = "industrious thing").
# Dropped ONLY when trailing AND not the sole word, so "number"/"creature" alone
# are preserved. This aligns rule-translator and LLM-translator outputs that
# differ only by such a classifier, which otherwise collapses to "ambiguous".
_LIGHT_HEADS = {"number", "creature", "thing", "object", "entity", "being", "individual"}


def pred_key(name: str) -> str:
    """Morphology-insensitive key: lemmatize each word so Tax/Taxation/taxes,
    Mortal/Mortality, etc. collapse to one predicate. Trailing light head nouns
    are dropped so PrimeNumber and Prime map together."""
    ws = words_of(name)
    if len(ws) > 1 and ws[-1] in _LIGHT_HEADS:
        ws = ws[:-1]
    return "".join(lemma(w) for w in ws)


def _pred_gloss(words: list[str]) -> str:
    """A minimal NL gloss of a unary predicate, for the semantic judge:
    ['fellow'] -> 'something is a fellow'; ['fellow','of','academy'] ->
    'something is a fellow of academy'."""
    body = " ".join(words)
    article = "an" if words and words[0][:1] in "aeiou" else "a"
    return f"something is {article} {body}"


class Vocabulary:
    def __init__(self, judge=None) -> None:
        self._pred_by_key: dict[str, str] = {}
        self._neg_of: dict[str, str] = {}
        self._const_by_key: dict[str, str] = {}
        # Optional SemanticJudge. When present, a brand-new predicate that shares
        # a head noun with an existing one (Fellow vs FellowOfAcademy) is checked
        # for coreference and, if the judge confirms it, aliased onto the existing
        # symbol so a rule and its instance phrased with/without the modifier
        # still meet inside Z3. Morphological canonicalization (below) runs first;
        # this only fires for the residue it cannot resolve, and never without a
        # judge -- so default behaviour is unchanged.
        self._judge = judge

    @property
    def predicates(self) -> list[str]:
        return sorted(set(self._pred_by_key.values()))

    @property
    def constants(self) -> list[str]:
        return sorted(set(self._const_by_key.values()))

    @property
    def negation_mappings(self) -> dict[str, str]:
        return dict(self._neg_of)

    def resolve_pred(self, name: str) -> tuple[str, bool]:
        """Return (canonical predicate, negated). Registers new predicates."""
        key = pred_key(name)
        if key in self._neg_of:
            return self._neg_of[key], True
        if key in self._pred_by_key:
            return self._pred_by_key[key], False
        ws = words_of(name)
        if len(ws) > 1 and ws[0] in ("not", "non"):
            base_key = "".join(lemma(w) for w in ws[1:])
            if base_key in self._pred_by_key:
                self._neg_of[key] = self._pred_by_key[base_key]
                return self._neg_of[key], True
        for p in NEG_PREFIXES:
            if key.startswith(p) and len(key) - len(p) >= 3 and key[len(p):] in self._pred_by_key:
                self._neg_of[key] = self._pred_by_key[key[len(p):]]
                return self._neg_of[key], True
        # Semantic alias (opt-in): before minting a fresh symbol, ask the judge
        # whether this predicate corefers with an existing one that shares its
        # head noun (modifier-folding divergence: Fellow vs FellowOfAcademy).
        if self._judge is not None:
            alias = self._semantic_alias(ws)
            if alias is not None:
                self._pred_by_key[key] = alias
                return alias, False
        # Display name: readable (plural-stripped surface form), not the
        # aggressively-lemmatized key. Matching uses the key; humans see this.
        camel = "".join(_plural_strip(w).capitalize() for w in ws)
        self._pred_by_key[key] = camel
        return camel, False

    def _semantic_alias(self, ws: list[str]) -> str | None:
        """Return an existing canonical predicate the judge deems coreferent with
        the new one `ws`, or None. Only predicates sharing the head noun and
        differing by a modifier are considered, keeping judge calls bounded."""
        if not ws:
            return None
        head = ws[0]
        new_gloss = _pred_gloss(ws)
        for existing_camel in dict.fromkeys(self._pred_by_key.values()):
            ew = words_of(existing_camel)
            if not ew or ew[0] != head or ew == ws:
                continue
            # one side must carry a modifier the other lacks (differ by length),
            # i.e. this is granularity divergence, not two distinct same-head ideas
            if len(ew) == len(ws):
                continue
            if self._judge.equivalent(new_gloss, _pred_gloss(ew)):
                return existing_camel
        return None

    def finalize_modifier_aliases(self) -> dict[str, str]:
        """Deterministic, document-scoped predicate unification (no LLM).

        Run once after every predicate is registered. Group canonical predicate
        names by head noun and merge a single modified form onto its bare head
        form: 'Fellow' + 'FellowOfAcademy' -> 'Fellow'. This is what reconnects a
        chain whose author wrote the consequent bare ('is a fellow') but the next
        rule's antecedent modified ('every fellow of the Academy ...').

        SAFETY: only fires when the head has EXACTLY ONE modifier variant beside
        exactly one bare form. 'ResidentOfFrance' + 'ResidentOfGermany' (two
        competing modifiers) are left untouched -- merging them, or either into a
        bare 'Resident', would fabricate a contradiction. Fully reproducible: the
        decision depends only on the set of predicates seen, not on any model.

        Returns the alias map {old_name: new_name} and rewrites the registry so
        `predicates`/`normalize_fol` report the merged symbol. Callers must also
        rewrite already-emitted FOL via `apply_pred_aliases`.
        """
        names = list(dict.fromkeys(self._pred_by_key.values()))
        by_head: dict[str, list[str]] = {}
        for n in names:
            ws = words_of(n)
            if ws:
                by_head.setdefault(lemma(ws[0]), []).append(n)

        aliases: dict[str, str] = {}
        for head, group in by_head.items():
            bare = [n for n in group if len(words_of(n)) == 1]
            modified = [n for n in group if len(words_of(n)) > 1 and lemma(words_of(n)[0]) == head]
            if len(modified) == 1 and len(bare) == 1:
                aliases[modified[0]] = bare[0]

        if aliases:
            for key, val in list(self._pred_by_key.items()):
                if val in aliases:
                    self._pred_by_key[key] = aliases[val]
            for key, val in list(self._neg_of.items()):
                if val in aliases:
                    self._neg_of[key] = aliases[val]
        return aliases

    def apply_pred_aliases(self, fol: str, aliases: dict[str, str]) -> str:
        """Rewrite predicate names in an already-normalized FOL string through the
        alias map from `finalize_modifier_aliases`. Only names in predicate
        position (immediately followed by '(') are touched."""
        if not aliases:
            return fol
        toks = tokenize(fol)
        out: list[str] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if _IDENT.match(t) and t not in KEYWORDS and nxt == "(" and t in aliases:
                out.append(aliases[t])
            else:
                out.append(t)
            i += 1
        return _detokenize(out)

    def finalize_self_reference_aliases(self) -> dict[str, str]:
        """Merge first-person self-reference constants onto one canonical entity
        (opt-in, single-author scope). A first-person document whose subject the
        extractor decontextualized as 'speaker' in one place and 'author' in
        another -- or a bridge premise an analyst wrote against 'author' while the
        text emitted 'speaker' -- should resolve to ONE constant, or a bridge
        cannot connect (the 'not RaiseTax(speaker)' vs 'RaiseTax(author)' miss).

        Canonical is 'author' when present, else the first self-ref token seen
        (deterministic). Only fires with >=2 distinct self-ref constants, so a
        document using a single label is untouched. SCOPE: single-author only --
        in a multi-speaker debate 'speaker' need NOT be the author, so this stays
        opt-in (Tier 5 will need per-speaker constants instead). Returns the alias
        map; callers rewrite emitted FOL via `apply_const_aliases`.
        """
        present = sorted({c for c in self._const_by_key.values() if c in _SELF_REF})
        if len(present) < 2:
            return {}
        canon = "author" if "author" in present else present[0]
        aliases = {c: canon for c in present if c != canon}
        for key, val in list(self._const_by_key.items()):
            if val in aliases:
                self._const_by_key[key] = aliases[val]
        return aliases

    def apply_const_aliases(self, fol: str, aliases: dict[str, str]) -> str:
        """Rewrite constant names through a self-reference alias map. Constants are
        identifiers in ARGUMENT position (not followed by '('), which also excludes
        bound variables -- and the map only holds self-ref tokens, never 'x'/'y'."""
        if not aliases:
            return fol
        toks = tokenize(fol)
        out: list[str] = []
        for i, t in enumerate(toks):
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if _IDENT.match(t) and t not in KEYWORDS and nxt != "(" and t in aliases:
                out.append(aliases[t])
            else:
                out.append(t)
        return _detokenize(out)

    def canonical_pred(self, name: str) -> str:
        return self.resolve_pred(name)[0]

    def canonical_const(self, name: str) -> str:
        key = name.lower()
        if key not in self._const_by_key:
            self._const_by_key[key] = key
        return self._const_by_key[key]

    def resolve_phrase(self, phrase: str) -> tuple[str, bool]:
        """'immortal' -> (Mortal, True) once Mortal is known; 'seekers of truth' -> (SeekerOfTruth, False)."""
        words = [w for w in re.split(r"[\s\-]+", phrase.strip().lower()) if w and w not in _STOPWORDS]
        words = [re.sub(r"[^a-z0-9]", "", w) for w in words]
        words = [w for w in words if w]
        if not words:
            raise ValueError(f"cannot build predicate from phrase {phrase!r}")
        if words[0] in ("not", "non") and len(words) > 1:
            name, neg = self.resolve_pred("".join(w.capitalize() for w in words[1:]))
            return name, not neg
        return self.resolve_pred("".join(w.capitalize() for w in words))

    def pred_from_phrase(self, phrase: str) -> str:
        return self.resolve_phrase(phrase)[0]

    def normalize_fol(self, fol: str) -> str:
        """Rewrite predicates/constants to canonical forms; negation-mapped
        predicates are emitted as `not P`."""
        toks = tokenize(fol)
        out: list[str] = []
        bound: set[str] = set()
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in ("forall", "exists"):
                out.append(t)
                i += 1
                while i < len(toks) and toks[i] != ".":
                    if toks[i] != ",":
                        bound.add(toks[i])
                    out.append(toks[i])
                    i += 1
                continue
            if _IDENT.match(t) and t not in KEYWORDS:
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt == "(":
                    canon, neg = self.resolve_pred(t)
                    if neg:
                        out.append("not")
                    out.append(canon)
                elif t in bound:
                    out.append(t)
                else:
                    out.append(self.canonical_const(t))
                i += 1
                continue
            out.append(t)
            i += 1
        return _detokenize(out)


def _detokenize(tokens: list[str]) -> str:
    out: list[str] = []
    prev = ""
    for t in tokens:
        if t in (")", ",", "."):
            out.append(t)
        elif t == "(":
            if prev and _IDENT.match(prev) and prev not in KEYWORDS:
                out.append(t)
            else:
                out.append(" " + t if out else t)
        else:
            out.append((" " if out and prev not in ("(",) else "") + t)
        prev = t
    return "".join(out).strip()
