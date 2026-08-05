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

# A code-like constant: a short letter tag glued to digits (a course number,
# id, etc.) -- 'c6100b', 'c6_5060'. The translator names ONE code many ways and
# these become DIFFERENT Z3 constants, silently breaking any chain that links
# them (the prereq-cycle false negative). Observed spellings of course '6.5060':
#   c65060   c6_5060   six_5060   six5060
# All must canonicalize to one entity. VERIFIED by controlled experiment: the
# model deterministically REUSES an existing 'six_5060' from the vocabulary
# (correct behaviour -- six_5060 IS 6.5060) while another statement coined the
# same course 'c65060', so 6.5060's node splits in two and the cycle never
# closes. The earlier underscore-only fix caught c6_5060 != c65060 but NOT the
# spelled-out-digit form.
_NUM_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_NUMWORD_PREFIX = re.compile(r"^([a-z]+)(_?\d.*)$")
_CODE_CONST = re.compile(r"[a-z]{0,3}\d")


def _const_key(name: str) -> str:
    """Morphology-insensitive constant key. Canonicalizes every spelling of a
    code-like constant to one VALID identifier: a leading number-word becomes its
    digit ('six_5060' -> '6_5060'), then the short letter tag and underscores are
    dropped and a stable 'c' tag prepended so the result is always parseable
    ('c6_5060', 'c65060', 'six_5060', 'six5060' -> 'c65060'; 'c6_100a' ->
    'c6100a'). Non-code names ('socrates', 'old_ferry', dates) are unchanged."""
    n = name.lower()
    # spelled-out leading digit: six_5060 / six5060 -> 6_5060 / 65060
    changed = False
    m = _NUMWORD_PREFIX.match(n)
    if m and m.group(1) in _NUM_WORDS:
        n = _NUM_WORDS[m.group(1)] + m.group(2)
        changed = True
    # Canonicalize only a GENUINE code with variant spellings: a letter tag on a
    # digit (or digit-led) AND (a converted number-word, or an inner underscore,
    # or a 2+-digit run). This leaves lone placeholders 'x0'/'s3' -- one digit,
    # one spelling, nothing to unify -- untouched.
    looks_code = bool(_CODE_CONST.match(n) or n[:1].isdigit())
    if looks_code and (changed or "_" in n or len(re.sub(r"[^0-9]", "", n)) >= 2):
        core = re.sub(r"[^0-9a-z]", "", n)             # drop underscores/punct
        core = re.sub(r"^[a-z]{1,3}(?=\d)", "", core)  # drop the letter tag
        return "c" + core                              # re-tag -> valid identifier
    return n
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

# ---------------------------------------------------------------------------
# Curated relational-synonym groups (the deterministic predicate merge).
#
# The translator names ONE relation off different surface forms -- ground facts
# off the verb ('6.100B requires 6.100A' -> Require) and rules off the noun
# ('A is a prerequisite for B' -> Prerequisite). Z3 sees two unrelated
# predicates, so a transitivity/irreflexivity rule ranges over a predicate with
# zero facts and a real cycle silently never forms (the rel_prereq_broken false
# negative). This table merges such synonyms DETERMINISTICALLY -- no LLM, no
# embeddings -- so the merge can never invent a relation the curator didn't
# hand-verify. Under-merge bias on purpose: a missed merge just keeps today's
# behavior; a wrong merge could manufacture a false contradiction.
#
# Each entry: content word (surface form or lemma) -> (group, flipped).
# `flipped` is the argument direction RELATIVE TO the group's base orientation:
#   prereq group base:  R(x, y) = "x requires y"
#     Require(b, a)        <- '6.100B requires 6.100A'         (not flipped)
#     Prerequisite(a, b)   <- 'A is a prerequisite for B'      (FLIPPED: b requires a)
#   containment group base: R(x, y) = "x is located in y"
#     LocatedIn(m, b)      <- 'Munich is located in Bavaria'   (not flipped)
#     Contains(b, m)       <- 'Bavaria contains Munich'        (FLIPPED)
# Merging rewrites every occurrence of a non-canonical member onto the group's
# canonical member, swapping the two arguments when their flip flags differ --
# so 'Prerequisite(a, b)' becomes 'Require(b, a)' and transitivity stays sound.
# EXTEND by adding words to a group (never antonyms/inverses as the same
# direction -- encode an inverse with the opposite flip flag, like 'contain').
_RELATION_SYNONYMS: dict[str, tuple[str, bool]] = {
    # --- prerequisite/dependency: base orientation "x requires y" ---
    "require": ("prereq", False), "requir": ("prereq", False), "required": ("prereq", False),
    "prerequisite": ("prereq", True), "prerequisit": ("prereq", True), "prereq": ("prereq", True),
    "depend": ("prereq", False),   # DependsOn(x, y): x depends on y == x requires y
    "need": ("prereq", False),     # Needs(x, y): x needs y == x requires y
    # --- spatial containment: base orientation "x is located in y" ---
    "locate": ("containment", False), "located": ("containment", False),
    "situate": ("containment", False), "situated": ("containment", False),
    "within": ("containment", False), "inside": ("containment", False),
    "contain": ("containment", True),  # Contains(x, y): x contains y == y located in x
}

# Function words dropped when reducing a predicate name to its content word:
# 'PrerequisiteFor' -> ['prerequisite'], 'LocatedIn' -> ['located'],
# 'DependsOn' -> ['depend']. A residue of MORE than one content word never
# matches (PushToRaiseTax stays untouched) -- multi-word names are ideas, not
# bare relation verbs.
_RELATION_PARTICLES = {
    "of", "for", "to", "on", "in", "at", "by", "with", "over", "upon", "from",
    "is", "are", "was", "were", "a", "an", "the",
}


def _relation_synonym_entry(name: str) -> tuple[str, bool] | None:
    """Map a canonical predicate name to its curated (group, flipped) entry, or
    None. Matches only when the name reduces to exactly ONE content word and
    that word (surface or lemma) is in the table."""
    ws = [w for w in words_of(name) if w not in _RELATION_PARTICLES]
    if len(ws) != 1:
        return None
    return _RELATION_SYNONYMS.get(ws[0]) or _RELATION_SYNONYMS.get(lemma(ws[0]))


def predicate_arities(fols: list[str]) -> dict[str, set[int]]:
    """Observed arities per predicate across a list of FOL strings (a predicate
    used inconsistently shows a set with more than one member)."""
    out: dict[str, set[int]] = {}
    for fol in fols:
        try:
            toks = tokenize(fol)
        except Exception:
            continue
        for i, t in enumerate(toks):
            if not (_IDENT.match(t) and t not in KEYWORDS
                    and i + 1 < len(toks) and toks[i + 1] == "("):
                continue
            depth, commas, content = 0, 0, False
            for tj in toks[i + 1:]:
                if tj == "(":
                    depth += 1
                elif tj == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif depth == 1:
                    if tj == ",":
                        commas += 1
                    else:
                        content = True
            out.setdefault(t, set()).add((commas + 1) if content else 0)
    return out


def _predicate_counts(fols: list[str]) -> dict[str, int]:
    """Number of STATEMENTS each predicate appears in (not raw token count --
    a transitivity rule mentions its predicate three times but is one
    statement; loyalty means keeping the surface form the author used in the
    most statements, which for a fact-heavy document is the facts' verb)."""
    counts: dict[str, int] = {}
    for fol in fols:
        try:
            toks = tokenize(fol)
        except Exception:
            continue
        present = {t for i, t in enumerate(toks)
                   if _IDENT.match(t) and t not in KEYWORDS
                   and i + 1 < len(toks) and toks[i + 1] == "("}
        for t in present:
            counts[t] = counts.get(t, 0) + 1
    return counts


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

    def finalize_relation_synonym_aliases(
        self, fols: list[str]
    ) -> tuple[dict[str, tuple[str, bool]], list[dict]]:
        """Deterministic, document-scoped RELATIONAL synonym merge (no LLM).

        Run once after every predicate is registered and every FOL is emitted
        (it needs the observed arities and usage counts). Within each curated
        _RELATION_SYNONYMS group, merge the distinct binary predicates the
        document actually used onto ONE canonical member, recording per-member
        whether the two arguments must be swapped (direction flip).

        SAFETY (under-merge bias -- a wrong merge could manufacture a false
        contradiction, a missed merge only keeps today's behavior):
        - curated table only; nothing fuzzy, no embeddings, no antonyms;
        - a member must reduce to exactly ONE curated content word;
        - a member must be used with arity EXACTLY 2 everywhere it appears;
        - negation-mapped predicates never participate;
        - fires only when a group has >= 2 distinct members in this document.

        LOYALTY: canonical = the member the document uses MOST (the author's
        dominant surface form; ties broken toward the group's base orientation,
        then alphabetically), so reports keep speaking the author's language.

        Returns ({old_name: (canonical, args_swapped)}, provenance list) and
        rewrites the registry so `predicates` reports the merged symbol.
        Callers rewrite emitted FOL via `apply_relation_synonym_aliases`.
        """
        arities = predicate_arities(fols)
        counts = _predicate_counts(fols)
        groups: dict[str, list[tuple[str, bool]]] = {}
        for name in dict.fromkeys(self._pred_by_key.values()):
            entry = _relation_synonym_entry(name)
            if entry is None:
                continue
            group, flipped = entry
            if arities.get(name) != {2}:      # strictly binary, used consistently
                continue
            groups.setdefault(group, []).append((name, flipped))

        aliases: dict[str, tuple[str, bool]] = {}
        provenance: list[dict] = []
        for group, members in groups.items():
            if len(members) < 2:
                continue
            # canonical: most-used surface form; tie -> base orientation, then name
            members.sort(key=lambda m: (-counts.get(m[0], 0), m[1], m[0]))
            canon, canon_flip = members[0]
            for name, flip in members[1:]:
                swap = flip != canon_flip
                aliases[name] = (canon, swap)
                provenance.append({
                    "from": name, "to": canon, "args_swapped": swap,
                    "reason": f"curated relational synonym (group '{group}'); "
                              f"kept the document's dominant form",
                })
        if aliases:
            plain = {old: new for old, (new, _) in aliases.items()}
            for key, val in list(self._pred_by_key.items()):
                if val in plain:
                    self._pred_by_key[key] = plain[val]
            for key, val in list(self._neg_of.items()):
                if val in plain:
                    self._neg_of[key] = plain[val]
        return aliases, provenance

    def apply_relation_synonym_aliases(
        self, fol: str, aliases: dict[str, tuple[str, bool]]
    ) -> str:
        """Rewrite predicate names through a relational-synonym alias map,
        swapping the two arguments where the direction flips. Arguments in the
        EPR fragment are single tokens (constants or bound variables), so the
        swap is a token-level exchange."""
        if not aliases:
            return fol
        toks = tokenize(fol)
        out: list[str] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if _IDENT.match(t) and t not in KEYWORDS and nxt == "(" and t in aliases:
                new, swap = aliases[t]
                # collect the two argument tokens: ( a , b )
                if (i + 5 < len(toks) and toks[i + 2] != ")" and toks[i + 3] == ","
                        and toks[i + 5] == ")"):
                    a, b = toks[i + 2], toks[i + 4]
                    if swap:
                        a, b = b, a
                    out.extend([new, "(", a, ",", b, ")"])
                    i += 6
                    continue
                out.append(new)   # unexpected shape: rename only, never reorder
            else:
                out.append(t)
            i += 1
        return _detokenize(out)

    def canonical_pred(self, name: str) -> str:
        return self.resolve_pred(name)[0]

    def canonical_const(self, name: str) -> str:
        key = _const_key(name.lower())
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
