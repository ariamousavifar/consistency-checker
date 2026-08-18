# Changelog

All notable changes to the Internal-Inconsistency Checker, newest first.
For what the project *is* and how to run it, see [README.md](README.md).

---

## Unreleased

### Evaluation: a baseline, a stress set, and a corrected report

No behaviour change and no source edits. This is measurement infrastructure and
the report built from it.

1. **`tools/baselines.py`, a reference point for the recall numbers.** A recall
   figure floats without something to compare it against. The baseline
   implemented here is sentence-pair entailment (NLI): every pair of statements
   judged independently, flagging the document if any pair conflicts. Each
   judgement sees only its two sentences. That isolation is the whole point, so
   pairs are never batched into one prompt, which would let the model reason
   across them and stop it being a pairwise method. Scored by the same rule and
   the same labels as the pipeline, so the numbers are directly comparable.

2. **`tools/make_stress_set.py`, pushing depth past what public corpora reach.**
   ProofWriter tops out at 5 inference steps. This generates documents at 5, 10,
   15 and 20 steps, crossed with 0, 40 and 100 irrelevant statements, so depth
   and document length vary independently. Distractors reuse the same predicate
   vocabulary on *other* entities rather than being off-topic filler, so they
   cannot be dismissed by surface similarity. The set is designed to be able to
   falsify the hypothesis it tests, and the prediction is recorded in `gold.json`
   before the run.

   The first version of this generator was wrong, and the fix is worth recording:
   names were drawn from 12 entity types × 20 tags, so two distinct objects could
   share a surface form ("consignment oscar" and "shipment oscar"), which the
   translator then collapsed into one, manufacturing contradictions that were
   artefacts of the test rather than the system. Names are now globally unique.
   Correcting it moved the measured false-positive rate from 18.8% to 10.4%.
   Only the corrected set is committed; the superseded one is ignored.

3. **`docs/evaluation.md` rewritten, and its central claim narrowed.** The
   previous version led with "recall does not degrade with reasoning depth". That
   is not what the stress set shows: recall is *flat* at roughly 50%, at every
   depth including the shallowest, which points at translation coverage rather
   than at depth. The report now says so, states plainly that translation and not
   solving is the bottleneck, and reports the ablation (80.2% → 12.5% with the
   language model withheld) as the evidence.

   The comparison it draws is explicitly against sentence-pair entailment, and
   the limitations section states that prompting a language model to judge a
   whole document is a serious alternative not evaluated here, so no reader
   mistakes the scope of the claim.

4. **`tools/evaluate.py`** gains `--no-chunk`, `--allow-relations` and
   `--allow-conditionals`. Chunking existed to fit a 5 requests-per-minute free
   tier and costs cross-chunk contradictions; it is no longer needed at current
   rate limits.

5. **Charts regenerated** (`tools/make_charts.py`), now plotting the pipeline
   against the pairwise baseline on the same axes. The stale figures from the
   previous report are removed rather than left to rot.

---

## Earlier unreleased work

Documentation and environment only. No behavior change, no source edits.

1. **README rewritten as a project description.** It had become a changelog under
   a README's filename: eight consecutive "What's new in v0.X" sections occupied
   the first 370 of 592 lines, so the first thing a visitor read was a bullet
   about layered entailment rather than a sentence explaining what the tool does.
   It was also stale, headed v0.8 while the code was v0.8.8. The new README
   leads with the problem being solved, shows two real worked examples (a direct
   three-statement contradiction, and a multi-hop prerequisite cycle that no
   single sentence states), a mermaid architecture diagram that visually
   separates the LLM stages from the deterministic ones, install/quickstart that
   foregrounds offline mode, and an explicit account of what the tool refuses and
   why. Version history moved here, verbatim, losing nothing.
2. **`docs/assets/`** now holds the rendered theory-tree graph (PNG + SVG) used by
   the README, taken from a real prerequisite-cycle run. Committed because
   `results/` is gitignored and GitHub cannot render an image it does not have.
3. **Working notes kept out of the repository.** A detailed internal handoff
   document (architecture tour, measured failure analyses, traps that look like
   bugs but are not, open work) is maintained locally and gitignored. It is
   development scaffolding, not something a reader of this project needs, so it
   does not belong in the published tree.
4. **Environment re-verified on a rebuilt virtualenv.** `requirements.txt`
   specifies lower bounds only, so a fresh install now resolves to major versions
   beyond those the project was developed against: z3 4.x → **5.0.0.0**,
   openai 1.x → **2.53.0**, pytest 8.x → **9.1.1** (also pydantic 2.13.4,
   python-dotenv 1.2.2, on Python 3.12.13). The full suite passes unchanged at
   255 tests, and the offline pipeline reproduces the expected result, so these
   bumps are confirmed benign rather than merely assumed. The verified set is now
   recorded as a comment in `requirements.txt` so a future breaking bump can be
   isolated by pinning to it.
5. **`requirements.txt` now bounded on both sides.** Minor and patch updates still
   flow in; the next major does not. Without a ceiling the drift above happens
   silently on any fresh clone, and a different *solver* major is not a cosmetic
   difference for a tool whose output is a proof. The ceilings make such a jump a
   deliberate, reviewed act instead of something a clone inherits by accident.
   Verified: the bounds resolve to exactly the tested set and the suite stays
   green. The file documents how to raise a ceiling (bump, reinstall, run the
   suite plus one offline pipeline run, record the new verified set).

---

## v0.8.8

Relational false-negative fixes found by a live tier-3 campaign, plus
statement-level translation resume. All deterministic; 255 tests pass.

1. **Directional relational-synonym merge** (`vocabulary.py`). The translator
   named one relation off different surface forms (ground facts off the verb
   (`Require`), universal rules off the noun, `Prerequisite`), so a transitivity
   rule ranged over a predicate with zero facts and a real prerequisite cycle
   silently never closed. A curated table now merges such synonyms *with argument
   direction*: "X requires Y" == "Y is a prerequisite for X", so `Prerequisite(a,b)`
   is rewritten `Require(b,a)`. Curated-only (no embeddings), strict arity-2,
   under-merge bias, canonical = the form the author used in the most statements.
2. **Guarded-irreflexivity normalization** (`normalize.py`). `forall x. (Person(x)
   -> not Ancestor(x,x))` in a document with zero `Person(...)` facts is vacuously
   true, so a 14-generation ancestry cycle went unrefuted. The dangling guard is
   now stripped, but *only* for that exact shape with a provably uninstantiated
   guard. Deliberately narrow: a general "strip guards" rule would manufacture
   contradictions ("all unicorns are immortal"), and a negative test locks that in.
3. **Constant-spelling unification** (`vocabulary._const_key`). One course number
   was coined four ways (`c65060`, `c6_5060`, `six_5060`, `six5060`), splitting a
   cycle node across distinct Z3 constants. All spellings now canonicalize to one
   valid identifier (number-word prefixes map to digits). Companion fix in
   `fidelity.py` so spelled-out forms match the source sentence.
4. **Statement-level translation resume** (`extraction.py`). Each parsed
   translation is checkpointed to `translation.partial.jsonl` (content-hash keyed,
   parsed-only, torn-line tolerant). Re-running into the same `--out` directory
   resumes instead of re-translating, so a rate-limit lockout hours into a
   book-length run loses nothing, and a provider swap mid-run is possible.
   Disable with `LLM_TRANSLATION_CACHE=0`.
5. **Campaign test harness** (`tests/campaign.py`), the full pre-release matrix as
   named, resumable tests. **Run outputs moved to `results/`**, keeping the repo
   root clean.
6. **`t3_bush_1988_notaxes` reclassified to expect 0 contradictions.** A broken
   promise ("I will not raise taxes" then raising them) is hypocrisy across time,
   not `P and not-P` in tenseless FOL; quarantining the modal statements is the
   correct behavior. It is now the canonical modality-out-of-scope example.

Experimental, off by default: `LLM_PREDICATE_GROUNDING=1` appends a permissive
predicate-reuse instruction to the translation prompt.

---

## What's new in v0.8

v0.8 turns the tool from a contradiction *flagger* into an argument
*reconstructor*: it rebuilds the author's derivation tree (axioms → theorems →
theorems-from-theorems), captures proof-by-contradiction, and is hardened to take
dense real prose (speeches, philosophy, op-eds) into logic. The argument-tree
machinery is validated end-to-end on a live geometry theory-tree run (a layered
axiom→theorem chain plus a reductio). On a *consistent* author it now shows the
proof structure, not just "no contradictions found"; finding consistency the
right way matters as much as finding inconsistency.

### Reconstructing the argument

1. **Theory trees, not fans (layered entailment).** The solver used to test each
   claim only against the axioms, so the support graph was a flat star: every
   theorem hung directly off the axioms with no theorem→theorem edges. It now grows
   a `proven` set, so each established theorem becomes available as a premise for
   later ones, and attributes a claim's support to the *deepest* (most compressed)
   intermediate theorem. `square→quadrilateral` is shown following from the theorem
   `square→parallelogram` plus one axiom, not flatly from three axioms. Genuine
   multi-level derivation trees now render in `graph.png`/`theory_tree.txt`.
2. **Asserted-premise roots.** A foundational premise the author states without
   deriving (which the extractor often types `derived_claim`, not `axiom`) is
   promoted to a root of the argument so the claims that follow from it actually
   derive, instead of the branch collapsing to `not_entailed` for want of an axiom
   label.
3. **Reductio ad absurdum.** Hypothetical suppositions are no longer discarded:
   they are translated and carried to the solver as *assumptions*, kept out of the
   asserted-theory consistency base. A supposition that contradicts the established
   theory is a successful reductio, because its negation is proven, reported with the new
   `refuted` verdict (`RA` mark, a dedicated report section, a `REDUCTIO` console
   line). The author's deliberate "assume the opposite" move is never misreported
   as the author contradicting himself. Verdicts are now six-valued.
4. **Semantic deduplication.** Over-extraction produces near-duplicate sentences
   whose FOL is identical modulo bound-variable names. They collapse to one
   canonical node (alpha-normalized key), so duplication can no longer manufacture
   spurious "X proved from X" derivation edges or inflate counts. Nothing is
   dropped: the duplicate stays in the report, excluded with a pointer to its
   canonical.

### Getting dense real prose into logic

5. **Conditional & deontic translation (`--allow-conditionals`).** An opt-in
   relaxed prompt stops nulling if/then/either-or structure, emitting `->`/`or`
   directly, and reifies normative claims ("entitled to", "must", "ought") into
   modality-named predicates so a norm never silently clashes with a plain fact.
   This is what lets a conditional argument (the spine of any real essay) reach the
   solver. Off by default; the base prompt is byte-identical to v0.7.
6. **Is/ought guard (`--guard-deontic`).** Optionally quarantine prescriptive
   statements so norms stay out of the descriptive axiom set, the control knob for
   is/ought false positives once deontic content is admitted.
7. **Self-reference unification (`--unify-self-ref`).** Merge first-person
   constants (author/speaker/I/…) to one entity so a bridge premise written against
   `author` connects to text that emitted `speaker`. Single-author scope.
8. **Fidelity stops punishing reuse.** The lexical-fidelity gate exempts the words
   of an already-established (reused) predicate from the invention penalty: a
   predicate coined and grounded earlier, then reused in a later conclusion phrased
   differently, no longer reads as low coverage and get quarantined. Freshly
   *invented* ungrounded predicates still fail, so real mistranslations are still
   caught.
9. **Generic/hedge guard + quarantine-shape instrumentation** (`src/linguistics.py`).
   Defeasible generalizations ("birds typically fly", "as a rule …") are quarantined
   before translation so a generic-with-exceptions can't become a strict `forall`
   and manufacture a false contradiction. A companion classifier buckets every
   outside-fragment statement (relational-ground / modal-deontic / comparative / …)
   into a histogram that measures, from real documents, which logic extension (EPR
   vs description logic vs modal) is the highest-leverage thing to build next.

### Reproducibility & model control

10. **Per-stage reasoning effort.** `LLM_EXTRACTION_EFFORT` and
    `LLM_TRANSLATION_EFFORT` set the reasoning depth of the extraction and
    translation stages independently: run extraction lean (reliable, cheap, under
    the token budget) while translation runs deep (where conditional reasoning
    pays off). Resolves the dense-document failures where one global effort either
    starved translation (under-thinking → null FOL) or blew the budget on
    extraction (empty JSON / HTTP 413).
11. **Seed & temperature; truthful effort reporting.** `--seed` / `--temperature`
    (also `LLM_SEED` / `LLM_TEMPERATURE`) for reproducible runs; the run header now
    reports the *effective* reasoning effort including env overrides (it used to
    print the registry default and mislead).
12. **Deterministic modifier-divergence resolution (NLI retired from the hot
    path).** A live experiment showed a blanket NLI judge was net-negative
    (over-quarantined faithful relational squashes, ~10× slower gate). It was
    replaced by deterministic, reproducible machinery: a modifier-only-divergence
    check in the gate (`FellowOfAcademy` vs `Fellow` → same skeleton, kept without
    an LLM) and a document-scoped unique-modifier merge in the vocabulary
    (`FellowOfAcademy` → `Fellow` only when exactly one modifier variant exists, so
    `ResidentOfFrance`/`ResidentOfGermany` stay untouched). NLI (`--nli`) is now
    scoped to genuine two-candidate adjudication only.
13. **Per-statement translation retry.** The batch translator drops hard
    sentences non-deterministically, so a conditional premise returns `null` in one
    run and valid FOL in another. After the batch pass, every statement that
    produced no *parseable* FOL is re-asked individually (an isolated
    single-statement prompt with the full accumulated vocabulary and a fresh
    draw) and recovers many of them. A retry only replaces a failure with a real
    success; a still-failing statement is left for the gate to quarantine. On by
    default; `LLM_TRANSLATION_RETRY=0` disables it, `LLM_TRANSLATION_RETRY_EFFORT`
    (default `medium`; `high` adds recall at roughly double the cost) tunes it.
    It recovers conditional premises; relational ones (`G owns R`) still need the
    EPR fragment.

### New CLI / env

```
python -m src.main --file doc.txt --allow-conditionals          # admit conditional/deontic structure
python -m src.main --file doc.txt --allow-conditionals --guard-deontic
python -m src.main --file speech.txt --bridges b.json --unify-self-ref
LLM_EXTRACTION_EFFORT=low LLM_TRANSLATION_EFFORT=medium python -m src.main --file doc.txt --allow-conditionals
python -m src.main --file doc.txt --seed 7 --temperature 0
```

New Tier-3 examples (single-author real text): a TED-talk transcript and a
Rothbard excerpt as false-positive controls, a 1988 political speech with a
cross-time bridge axiom, and a geometry theory-tree text (a layered
axiom→theorem chain plus a reductio) that exercises the tree reconstruction.

Test suite expanded to 184 offline tests.

### Known frontier (honest)

The argument-tree machinery is complete and proven on clean, claim-dense text.
On *dense* prose (e.g. the Rothbard excerpt) the binding constraint is upstream
translation reliability: the conditional premises that would form a real
multi-step tree translate non-deterministically (sometimes valid FOL, sometimes
`null`), so the tree can be shallow even though the engine is correct. The
per-statement translation retry (item 13) is the first attack on this; recovering
the remaining relational premises (ownership, rule-over) needs the relational
(EPR) fragment, which is the next logic extension the quarantine-shape histogram
points to.

## What's new in v0.7

1. Document chunking. Long documents (real Wikipedia/SEP pages, transcripts) are
   split into bounded chunks, each extracted independently, then all statements
   are pooled into ONE solver run. A contradiction spanning chunk 1 and chunk 9
   is still found because the solver sees the whole belief set at once. Short
   documents take the original single-pass path unchanged. Chunk boundaries are
   paragraph-based with a small overlap for seam context; the boundary detector
   is pluggable so a transcript-aware splitter can drop in later.
2. Resumable processing. Each chunk's extraction result is cached to
   <out>/chunks/chunk_NNN.json as it completes. If a run dies partway (rate
   limit, timeout, machine sleep), rerun with --resume <out_folder> to reuse the
   finished chunks and only process the missing ones. Verified: a resumed run
   makes zero LLM calls for already-cached chunks and produces identical output.
3. Tier selection. --tier N runs only examples tagged with that tier in
   examples.json; --all-examples is unchanged (runs everything). Output folders
   are tagged out_all_<stamp>_tierN_effort1/.
4. Cerebras fixes: correct bare model IDs (gpt-oss-120b, llama-3.3-70b, ...) and
   the max_completion_tokens parameter Cerebras requires.
5. Empty-response guard: when a model returns a completely blank reply (seen on
   Cerebras with the long SEP text), the retry resends the original task cleanly
   instead of a pointless "your output wasn't JSON" correction.
6. Tier 2.5 chunking stress tests (t25a/b/c) with an answer key: long synthetic
   texts whose planted contradictions SPAN chunk boundaries, proving chunking
   preserves both direct and multi-hop detection. t25a is long-but-consistent
   (false-positive check); t25b is a 1-hop cross-chunk contradiction; t25c is a
   3-hop cross-chunk contradiction.
7. Test suite expanded to 104 offline tests.

### New CLI

```
python -m src.main --all-examples                 # everything (unchanged)
python -m src.main --tier 1                        # only tier-1 examples
python -m src.main --tier 25                        # the chunking stress tests
python -m src.main --resume out_all_20260616_..._effort1   # resume a dead run
python -m src.main --file examples/long_doc.txt    # single file (auto-chunks if long)
```



## What's new in v0.6.2

1. Self-correction context fix (the important one). When a model's first reply
   to a long document was not valid JSON, the retry prompt used to REPLACE the
   document with "re-emit the same content" - so the model lost the text and
   answered "I don't have the content you're referring to," failing the example.
   The retry now keeps the original document and appends the correction
   instruction. This is what crashed the real Wikipedia (Neptune) and SEP
   (stanford) examples on gpt-oss-120b; they now complete.
2. Gemini corrections: current model IDs (gemini-3.5-flash, gemini-3.1-flash-lite,
   gemini-3.1-pro, gemini-2.5-flash) replacing the stale 2.0/1.5 guesses, and
   per Google's Gemini 3.x guidance the client now omits temperature/top_p for
   Google models (their reasoning is tuned for defaults; sending sampling params
   is discouraged). Every other provider still gets temperature 0 for determinism.
3. Test suite expanded to 92 offline tests.

### Practical note on free-tier rate limits

The v0.6.1 retry handling works (it reads and obeys retry-after), but some free
tiers are too strict for an interactive 18-example batch - especially the
thinking models (DeepSeek Pro, Qwen, Gemma) and Gemini, which can force 45-60s
waits per call. For full batches, use a fast non-thinking model on a generous
tier: gpt-oss-120b on Groq is the reliable choice. Reserve the thinking models
and Gemini for spot-checking a few examples.



## What's new in v0.6.1

1. Rate-limit handling. The LLM client now (a) throttles calls to a minimum
   interval (LLM_MIN_INTERVAL, default 1.5s) so a burst stays under free-tier
   RPM caps, (b) on a 429 waits and retries up to LLM_MAX_RETRIES times, and
   (c) honors the server's retry-after header (numeric seconds or Groq-style
   '1m11s' durations). This is what lets an 18-example batch complete on a
   40-requests/minute free tier instead of erroring out partway.
2. Two more providers in the picker: Cerebras (fast, 1M tokens/day) and Google
   AI Studio / Gemini (1500 requests/day). Together with Groq and NVIDIA NIM
   that is four model families for a cross-model robustness comparison.
3. Test suite expanded to 87 offline tests.

### Note on slow reasoning models

DeepSeek V4 Pro, Qwen 3.5, and Gemma 4 (the NIM 'thinking' models) are much
slower per call (20-30s each) and burn rate-limit budget fast. For full batches
prefer a fast non-thinking model (gpt-oss-120b on Groq or NIM, or Cerebras
llama-3.3-70b). The thinking models still work, just pace them with a higher
LLM_MIN_INTERVAL.



## What's new in v0.6

Driven by the NVIDIA NIM + Groq live runs across the full Tier-1/Tier-2 battery:

1. Extraction robustness: malformed LLM output no longer crashes an example.
   A statement returned as a bare string, a list wrapped in a dict, or stray
   null/garbage items are now coerced or skipped (parse_statements). This kills
   the recurring "argument after ** must be a mapping, not str" crash that
   aborted the Neptune/stanford examples.
2. Instance retyping: a bare ground instance ("The blue is a whale",
   "This document is a contract") with no derivation marker is retyped from
   derived_claim to AXIOM, so it enters the axiom base instead of collapsing the
   whole entailment chain to not_entailed. Conservative: statements carrying
   therefore/thus/so/hence keep their derived_claim type. Fixes the t1/t2/t2e
   entailment collapses seen on NIM.
3. Adaptive fidelity threshold: single-predicate statements ("Old Ferry has no
   population") no longer get quarantined just because the lone constant or an
   auxiliary word fails to match. Recovers t2e's contradicting sentence.
4. Provider/model picker: choose at run time via
   --provider nim --model qwen3.5-122b-a10b, or run with no flags for an
   interactive numbered menu in the terminal, or fall back to .env. providers.json
   lists the endpoints and models; keys stay in .env (NIM_API_KEY / GROQ_API_KEY).
5. Reasoning-model support: DeepSeek V4, Qwen 3.5, and Gemma 4 are flagged as
   thinking models and called with thinking disabled (so they return clean JSON,
   not a buried chain-of-thought), with a reasoning_content fallback and a retry
   that drops extra_body if the endpoint rejects it. This unblocks the NIM models
   that previously failed.
6. Test suite expanded to 78 offline tests.

### Choosing a model

```
# explicit
python -m src.main --all-examples --provider nim --model qwen3.5-122b-a10b

# interactive menu (no flags)
python -m src.main --all-examples

# .env fallback (headless / piped)
python -m src.main --all-examples
```


## What's new in v0.5

Driven by the first real Tier-1/Tier-2 live runs, which exposed two SILENT
false-negative bugs (the tool said 'all clear' when a contradiction existed):

1. Fidelity check no longer over-quarantines correct translations. Multi-word
   constants ("the blue" -> theblue, "her conviction" -> herconviction) are
   split into component words before matching, and auxiliary/function words
   ("has", "by", "for") no longer count against coverage. This fixed Llama
   missing the 3-hop contradiction (t6) and the planted taxonomy break (t2b),
   both of which had been wrongly quarantined.
2. Multi-word predicate canonicalization. Trailing light head nouns ("prime
   NUMBER" vs "prime", "industrious CREATURE" vs "industrious") are dropped for
   matching, so the rule translator and the LLM translator stop disagreeing and
   collapsing to 'ambiguous'. This fixed t2f (inconsistent definitions) being
   missed on both models. "Number"/"creature" alone are preserved.
3. Graph conflict rendering fixed. A minimal inconsistent set is now drawn as a
   single hub (DOT) or enclosing band (SVG) joining all members, instead of
   pairwise red edges between every pair -- the old rendering drew a misleading
   triangle implying each statement contradicted each other, when in fact only
   the whole SET is jointly unsatisfiable.
4. Per-example error isolation in --all-examples. One example failing (e.g. a
   model returning prose instead of JSON, as GPT did on the Neptune text) is
   logged and skipped; the batch continues instead of aborting.
5. Consolidated transcript: --all-examples now writes
   out_all_<stamp>/all_examples_report.txt containing every example's full
   console output plus the summary table, so results need not be copy-pasted
   from each report.md by hand.
6. Test suite expanded to 62 offline tests, including explicit regression guards
   for both false-negative bugs.


## What's new in v0.4

1. Lemmatization in vocabulary alignment: Tax / Taxation / taxes now collapse
   to one predicate (also mortality/mortal, happiness/happy, etc.). This fixes
   the live-run failure where a Tax-vs-Taxation mismatch made a real
   contradiction invisible. Short words are protected from over-stripping.
2. Symmetric consistency checking: inconsistency is now a property of a SET,
   independent of statement role. Two derived claims that contradict each other
   are flagged even if neither is an axiom. The axiom/derived_claim distinction
   is kept only for entailment direction and display. When a set is
   inconsistent, the report still computes entailment context from the
   consistent remainder, so it explains WHY the conflict arises.
3. Deterministic compound splitting: a post-extraction pass splits statements
   that still join two independent clauses ("Socrates was a philosopher and
   Socrates was human") while preserving shared-predicate noun phrases ("roads
   and hospitals do not pay for themselves"). Applied in live mode; fixtures
   are authored pre-split.
4. Incomplete-argument reporting: a not_entailed claim is now explicitly framed
   as "the premises are insufficient to prove this; the argument may rely on
   unstated assumptions" -- an incomplete argument, distinct from inconsistency.
5. Effort dial extended to level 3: cross-cluster pairwise sweep that catches
   inconsistencies spanning clusters the predicate-clusterer split apart.
6. Z3 scaling instrumentation: each cluster reports statement count, solver
   milliseconds, and a timeout/unknown flag; a warning fires for large sets
   where quantifier instantiation becomes unpredictable.
7. --all-examples: runs every example in examples/examples.json into one
   timestamped folder with a named subfolder per example and a summary table
   (inconsistent sets found, screener flags, total ms per example).
8. --out now auto-increments (out, out1, out2, ...) so runs never overwrite.
9. Test suite expanded to 54 offline tests.

### Note on the sample essay result

Because the sample essay asserts both "Socrates was mortal" and "Socrates is
immortal", v0.4's symmetric checker now correctly reports the MINIMAL
inconsistent set as {s7, s8} (the direct contradiction) rather than the longer
multi-hop set, while still showing that s6 is entailed from the consistent
remainder. This is more correct, not a regression.


## What's new in v0.3

1. Per-stage timing: console table, `out/timing.json`, and a report section.
2. Neutral verdict language: the system now reports minimal inconsistent sets
   ("these statements cannot all be true; at least one must be abandoned")
   instead of implying that the contradicting claim is the wrong one. No
   member of a set is ever declared false.
3. Negation mapping in vocabulary alignment: `Immortal(x)` is rewritten to
   `not Mortal(x)` once `Mortal` is known (same for un-, non-, ir-, dis-, in-
   prefixed forms). This fixes the observed live-run failure where the
   contradiction silently vanished. Direction matters: the base predicate must
   appear first, which matches the usual premises-before-claims order.
4. Bridge premises: pass `--bridges file.json` to supply explicit, tagged
   background axioms (e.g. "all theft is morally unjustified"). Contradictions
   that need them are labeled "bridged" and the report names the premise, so
   the tool stays loyal to the text and never imports semantics silently.
5. Effort dial: `--effort 0` (surface screener only), `1` (clustered symbolic
   checks, default), `2` (one global axiom set: deeper cross-topic reasoning,
   higher timeout risk). The solver warns on large axiom sets.
6. Surface screener: a deterministic lexical placeholder for the future NLI
   path. It flags shared-wording/opposite-polarity pairs and prefix antonyms,
   runs at every effort level, and doubles as the embedded baseline; multi-hop
   inconsistencies are invisible to it by design.
7. Hardened prompts: explicit axiom-vs-derived_claim rules, mandatory
   compound-sentence splitting, and a ban on inventing antonym predicates.
8. Test suite expanded from 17 to 37 offline tests.


A neuro-symbolic pipeline that finds internal logical inconsistencies in a text
using only the author's own statements: no agreement on which axioms are
"correct" is needed. If the author's statements are mutually inconsistent, they
have already violated their own logical standards. The LLM only does natural
language judgment and translation; Z3 does all logical verification.

