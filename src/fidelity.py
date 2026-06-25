"""Fidelity check (v1.1).

The target design is: deterministic verbalization + bidirectional NLI
entailment. v1 ships the deterministic half plus a lexical-coverage heuristic
instead of the NLI model, to keep the prototype dependency-light. The check
asks: do the content words of every predicate, and every constant, actually
appear (approximately) in the source proposition? It catches the most common
silent translation failure: the LLM inventing predicates or entities that the
sentence never mentioned.

v1.1 fixes over-quarantining that produced false negatives in live runs:
- Multi-word constants ("the blue" -> theblue, "her conviction" -> herconviction)
  are split into their component words before matching, instead of being treated
  as one unmatchable token.
- Constants are scored into the same soft coverage ratio as predicate words,
  rather than a hard all-or-nothing gate. A single multi-word constant no longer
  fails an otherwise perfect translation.
- Function/auxiliary words ("has", "have", "by", "for"...) are excluded from the
  missing-word penalty: they carry no logical content and their absence from a
  predicate name is not a fidelity problem.

Swap-in point for the NLI model: implement fidelity_check with a cross-encoder
and keep the same return type.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .fol_parser import KEYWORDS, tokenize
from .lemmatizer import lemma
from .vocabulary import NEG_PREFIXES, words_of

# Words that carry no logical content; their absence from a predicate/constant
# name must never cause a fidelity failure.
_STOP = {
    "a", "an", "the", "of", "is", "are", "be", "to", "in", "on", "at", "by",
    "for", "with", "that", "this", "these", "those", "it", "its", "their",
    "his", "her", "has", "have", "had", "was", "were", "and", "or", "as",
    "from", "into", "they", "them", "some", "every", "all", "any", "no",
}


@dataclass
class FidelityResult:
    passed: bool
    coverage: float
    missing: list[str] = field(default_factory=list)
    verbalization: str = ""


def _split_const(name: str) -> list[str]:
    """Break a constant token into component words.

    Constants arrive as lowercased run-together or underscored tokens:
    theblue, her_conviction, old_ferry, thisdocument, date_2000_10_26.
    We split on underscores and on a small set of leading determiners/pronouns
    so the pieces can match the source sentence.
    """
    raw = re.split(r"[_\s]+", name.lower())
    out: list[str] = []
    for piece in raw:
        if not piece:
            continue
        # peel common leading determiners/pronouns glued to the front
        for det in ("thisdocument",):  # exact common case
            if piece == det:
                out.extend(["this", "document"])
                piece = ""
                break
        if not piece:
            continue
        matched = False
        for det in ("the", "this", "that", "her", "his", "their", "an", "a"):
            if piece.startswith(det) and len(piece) - len(det) >= 3:
                out.append(det)
                out.append(piece[len(det):])
                matched = True
                break
        if not matched:
            out.append(piece)
    return [w for w in out if w]


def _content_words(words: list[str]) -> list[str]:
    return [w for w in words if w not in _STOP and not w.isdigit() and len(w) >= 2]


# A code-like constant: a short letter tag (0-3 chars) glued to digits, the form
# the translator coins for identifiers like course numbers -- '6.100B' -> c6100b,
# '6.5060' -> c6_5060. Its DIGITS are the content, so the word-split path (which
# drops pure-digit pieces) wrongly quarantines it. Match its alphanumeric
# signature against the sentence's punctuation-stripped text instead.
_CODE_RE = re.compile(r"[a-z]{0,3}\d[\da-z]*$")


def _is_code(tok: str) -> bool:
    return bool(_CODE_RE.fullmatch(re.sub(r"[^a-z0-9]", "", tok.lower())))


def _code_forms(tok: str) -> set[str]:
    """Alphanumeric signatures of a code-like constant: the run-together form and,
    if a lone letter tag precedes the digits ('c6100b'), the de-tagged form
    ('6100b'). Either may appear in the joined sentence ('6.100B' -> '...6100b...')."""
    s = re.sub(r"[^a-z0-9]", "", tok.lower())
    forms = {s}
    if len(s) >= 2 and s[0].isalpha() and s[1].isdigit():
        forms.add(s[1:])
    return {f for f in forms if len(f) >= 2}


def _symbols(fol: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return (predicate content words tagged with their predicate symbol,
    raw constant tokens) from FOL. The symbol tag lets the caller exempt
    words that belong to a REUSED predicate (vetted when first coined) from the
    invention penalty. Constants are returned RAW (un-split) so the caller can
    route code-like ones (course numbers) to signature matching and word-like
    ones to per-word coverage."""
    toks = tokenize(fol)
    pred_words: list[tuple[str, str]] = []
    consts: list[str] = []
    bound: set[str] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("forall", "exists"):
            i += 1
            while i < len(toks) and toks[i] != ".":
                if toks[i] != ",":
                    bound.add(toks[i])
                i += 1
            continue
        if re.match(r"[A-Za-z_]\w*$", t) and t not in KEYWORDS:
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt == "(":
                pred_words.extend((w, t) for w in _content_words(words_of(t)))
            elif t not in bound:
                consts.append(t)
        i += 1
    return pred_words, consts


def _word_match(word: str, sentence_lemmas: set[str]) -> bool:
    wl = lemma(word)
    if wl in sentence_lemmas or word in sentence_lemmas:
        return True
    # prefix fallback for shared roots the lemmatizer doesn't catch
    if len(wl) >= 4:
        return any(sw.startswith(wl[:4]) or wl.startswith(sw[:4])
                   for sw in sentence_lemmas if len(sw) >= 4)
    return False


def fidelity_check(fol: str, sentence: str, threshold: float = 0.6,
                   known_preds: set[str] | None = None) -> FidelityResult:
    """Lexical fidelity only. The NLI judge is deliberately NOT wired in here:
    a live experiment showed that judging every single-candidate translation
    with the model over-quarantined faithful-but-lossy verb-object/relational
    squashes (removing real chain links) and slowed the gate ~10x. The judge
    now fires only where there is an actual divergence to adjudicate -- the
    two-candidate branch in gate.py -- not on every statement here.

    `known_preds`: predicate symbols already established by EARLIER statements.
    Fidelity's job is to catch INVENTION (a predicate the sentence never
    mentions), but a reused symbol was grounded in the sentence that first
    coined it -- a later statement that reuses it needn't re-mention its words.
    Without this, vocabulary reuse (which is what connects a long argument's
    chain) reads as low coverage and quarantines correct conclusions: e.g.
    'RightToFullBodyOwnership' coined at an earlier premise, reused in the
    'therefore' conclusion whose sentence says 'self-ownership' (no 'body')."""
    from .verbalizer import verbalize

    known_preds = known_preds or set()
    raw_words = re.findall(r"[a-z0-9]+", sentence.lower())
    sentence_lemmas = {lemma(w) for w in raw_words} | set(raw_words)
    # punctuation-stripped run for code-like constants: '6.100B requires 6.100A'
    # -> '6100brequires6100a', so 'c6100b' (signature '6100b') matches.
    joined_sentence = re.sub(r"[^a-z0-9]+", "", sentence.lower())
    for sw in list(sentence_lemmas):
        for pfx in NEG_PREFIXES:
            if sw.startswith(pfx) and len(sw) - len(pfx) >= 3:
                sentence_lemmas.add(sw[len(pfx):])

    pred_pairs, consts = _symbols(fol)
    pred_words = [w for w, _ in pred_pairs]
    missing: list[str] = []

    matched = 0
    total = 0
    for w, sym in pred_pairs:
        total += 1
        # A word from a reused (already-established) predicate is vetted; exempt.
        if sym in known_preds or _word_match(w, sentence_lemmas):
            matched += 1
        else:
            missing.append(w)
    for c in consts:
        if _is_code(c):
            # one unit: its digits are the identity, matched as a signature
            total += 1
            if any(f in joined_sentence for f in _code_forms(c)):
                matched += 1
            else:
                missing.append(c)
        else:
            for w in _content_words(_split_const(c)):
                total += 1
                if _word_match(w, sentence_lemmas):
                    matched += 1
                else:
                    missing.append(w)
    coverage = matched / total if total else 1.0

    # Adaptive threshold: a single-predicate statement ("Old Ferry has no
    # population" -> not HasPopulation(old_ferry)) has very few content words, so
    # one stopword-ish miss tanks the ratio. When there is exactly one predicate
    # and its content word(s) matched, relax the bar so a lone unmatched constant
    # fragment does not wrongly quarantine a correct translation.
    effective_threshold = threshold
    if len(set(pred_words)) <= 1 and pred_words and all(_word_match(w, sentence_lemmas) for w in pred_words):
        effective_threshold = min(threshold, 0.5)

    try:
        verb = verbalize(fol)
    except Exception:
        verb = "(verbalization failed)"

    return FidelityResult(
        passed=coverage >= effective_threshold,
        coverage=round(coverage, 3),
        missing=missing,
        verbalization=verb,
    )
