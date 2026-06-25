# Internal-Inconsistency Checker (prototype v0.8)

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
   a `proven` set — each established theorem becomes available as a premise for
   later ones — and attributes a claim's support to the *deepest* (most compressed)
   intermediate theorem. `square→quadrilateral` is shown following from the theorem
   `square→parallelogram` plus one axiom, not flatly from three axioms. Genuine
   multi-level derivation trees now render in `graph.png`/`theory_tree.txt`.
2. **Asserted-premise roots.** A foundational premise the author states without
   deriving (which the extractor often types `derived_claim`, not `axiom`) is
   promoted to a root of the argument so the claims that follow from it actually
   derive — instead of the branch collapsing to `not_entailed` for want of an axiom
   label.
3. **Reductio ad absurdum.** Hypothetical suppositions are no longer discarded:
   they are translated and carried to the solver as *assumptions*, kept out of the
   asserted-theory consistency base. A supposition that contradicts the established
   theory is a successful reductio — its negation is proven — reported with the new
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
   relaxed prompt stops nulling if/then/either-or structure — it emits `->`/`or`
   directly — and reifies normative claims ("entitled to", "must", "ought") into
   modality-named predicates so a norm never silently clashes with a plain fact.
   This is what lets a conditional argument (the spine of any real essay) reach the
   solver. Off by default; the base prompt is byte-identical to v0.7.
6. **Is/ought guard (`--guard-deontic`).** Optionally quarantine prescriptive
   statements so norms stay out of the descriptive axiom set — the control knob for
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
    translation stages independently — run extraction lean (reliable, cheap, under
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
    sentences non-deterministically — a conditional premise returns `null` in one
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

## What the example run shows

```
python -m src.main --file examples/sample_essay.txt --offline
```

Run every shipped example at once into one timestamped folder:

```
python -m src.main --all-examples --offline
```

A second example demonstrates bridged contradictions (taxation):

```
python -m src.main --file examples/taxation_essay.txt --offline --bridges examples/taxation_essay.bridges.json
```

Without the bridge file, "some taxes are morally justified" is merely
unprovable; with the explicit background premise "all theft is morally
unjustified", it joins a minimal inconsistent set {t1, b1, t3}, labeled as
bridged. The shipped essay asserts five premises, two claims that genuinely follow from
them, one multi-hop contradiction ("Socrates is immortal", which conflicts with
his being a philosopher, philosophers being human, and humans being mortal),
one figurative sentence, and one claim that is consistent but unprovable. The
pipeline reproduces exactly that analysis:

- s6, s7: `entailed`, each with its minimal supporting axiom set
- s8: `contradicts`, with the minimal conflicting set {s3, s4, s5} and source offsets
- s10: `not_entailed` (consistent with the axioms but not provable from them)
- s9: `quarantined` with an explicit reason (figurative, not truth-apt); nothing is dropped silently

Outputs land in `out/`:

- `report.md` (human-readable report, includes the theory tree), `report.json`, `store.json`
- `theory_tree.txt`: ASCII tree of the theory: axioms, claims, each claim's proof
  support or minimal conflicting set, and everything excluded with its reason
- `graph.svg`: color-coded diagram (blue axioms, green entailed, amber
  not_entailed, red contradicts, dashed gray excluded; gray edges = proof
  support, red dashed edges = minimal conflict). Standalone, zero
  dependencies: open it in any browser or click it in PyCharm.
- `graph.dot`: Graphviz source for the same graph (dependency layout)
- `graph.png`: rendered automatically IF the Graphviz `dot` binary is
  installed on your machine (https://graphviz.org/download/); otherwise
  skipped silently: the .svg needs nothing installed.

The tree is also printed to the console after the verdict table
(disable with `--no-tree`).

## Architecture (v1 slice of the full design)

```
raw text
  -> cleaning            deterministic; paragraphs, speaker turns, char offsets    src/cleaning.py
  -> extraction judge    LLM (or fixture): truth-aptness, statement type,          src/extraction.py
                         decontextualized self-contained rewrite                   src/prompts.py
  -> vocabulary          deterministic normalization of predicates/constants       src/vocabulary.py
  -> translation gate    rule translator + LLM translator -> Z3-proved             src/rule_translator.py
                         equivalence -> fidelity routing -> accept /               src/gate.py
                         ambiguous / quarantine                                    src/fidelity.py
  -> proposition store   per-statement record: FOL, status, confidence, spans      out/store.json
  -> solver              predicate-overlap clustering; consistency, entailment,    src/solver.py
                         contradiction; minimal conflict/support sets (Z3)         src/fol_parser.py
  -> reports             Markdown + JSON with provenance                           src/report.py
```

Design commitments carried through the code:

1. Provenance end to end. Every statement maps to character offsets in the
   original file; contradiction reports quote the original sentences.
2. Deterministic-first. The LLM appears only where judgment is unavoidable
   (extraction/typing, translating outside the rule fragment). The rule
   translator, vocabulary normalization, equivalence checking, fidelity
   verbalization, solving, and reporting are all auditable code.
3. Nothing enters the solver unverified. Statements are accepted, flagged
   ambiguous, or quarantined with a recorded reason. Quarantine is a report
   section, not a silent drop.
4. Six-valued verdicts: entailed / not_entailed / contradicts / refuted /
   unknown / error. `refuted` marks a hypothetical refuted by reductio (its
   negation proven), kept distinct from `contradicts` so the author's own
   "assume the opposite" is never reported as self-contradiction. Solver
   timeouts map to `unknown`, never misreported.
5. Minimal conflict sets. On contradiction, the unsat core is shrunk
   (deletion-based) to a minimal set of statements that cannot all be true,
   which is the actual product output.

## How to open and run in PyCharm

Requirements: Python 3.10+ (tested on 3.12), internet for `pip install`.

1. Unzip this archive somewhere, e.g. `~/projects/consistency-checker`.
2. PyCharm -> File -> Open... -> select the `consistency-checker` folder.
3. Interpreter: PyCharm usually offers to create a virtualenv from
   `requirements.txt`; accept it. Otherwise: Settings -> Project ->
   Python Interpreter -> Add Interpreter -> Virtualenv (new), then open the
   PyCharm Terminal and run:
   ```
   pip install -r requirements.txt
   ```
4. Run the offline demo (no API key needed). Run -> Edit Configurations ->
   "+" -> Python:
   - Mode: `module` (click "script path" dropdown and choose "module name")
   - Module name: `src.main`
   - Parameters: `--file examples/sample_essay.txt --offline`
     (or the taxation command above for the bridged demo)
   - Working directory: the project root (the folder containing `src/`)
   Press Run. The console prints the verdict table; open `out/report.md`
   to see the full report (PyCharm renders Markdown).
5. Run the tests: right-click the `tests/` folder -> "Run pytest in tests".
   All 181 tests run offline in about a second.

Command-line equivalents:
```
pip install -r requirements.txt
python -m pytest tests/ -q
python -m src.main --file examples/sample_essay.txt --offline
```

## Live mode (Groq or NVIDIA NIM)

1. Copy `.env.example` to `.env` and fill in your key. Both providers expose
   OpenAI-compatible endpoints, so only three variables matter:
   `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
2. Run without `--offline`:
   ```
   python -m src.main --file path/to/your_document.txt
   ```
3. Free-tier notes: keep `LLM_MAX_TOKENS=2048`, and start with short documents
   (v1 truncates extraction input at ~6000 characters to stay inside
   tokens-per-minute limits; chunked extraction is on the roadmap). Translation
   is already batched (8 statements per call) with the shared vocabulary passed
   forward. Temperature 0 is set for near-deterministic translation.

A good first live experiment: run the shipped essay in live mode and diff the
result against the offline fixtures, which double as a frozen regression
target for prompt and model changes.

## Offline fixtures

`examples/fixtures/` contains hand-made judgments for the example essay in the
exact schema the live prompts request: `sample_essay.extraction.json` plays
the extraction judge, `sample_essay.translation.json` plays the LLM
translator. This lets the entire downstream pipeline (gate, Z3, MUS
extraction, reporting) run with zero tokens, and pins expected behavior in the
end-to-end test.

## Honest limitations of v1 (by design, see roadmap)

- Fidelity check is a lexical-coverage heuristic over the deterministic
  verbalization, not yet bidirectional NLI entailment. It catches invented
  predicates/entities, not subtle meaning drift.
- The rule translator covers a small controlled fragment on purpose
  (precision-first; it refuses the rest).
- Plain-text input only; PDF/HTML cleaning modules are planned behind the same
  interface.
- FOL still cannot express causation, tense, or comparatives; the translator
  returns null for those and the gate quarantines them. Deontic/normative
  content is now reifiable into predicates under `--allow-conditionals` (modality
  carried in the predicate name, not as a true modal operator), and defeasible
  generics are deliberately quarantined by the hedge guard rather than forced
  into a strict `forall`.
- Single-speaker belief sets per run; `attributed` statements are excluded with
  a recorded reason. `hypothetical` statements are now modeled as reductio
  assumptions (kept out of the asserted base; a supposition that contradicts the
  theory is reported `refuted`).
- Vocabulary alignment is deterministic: plural/case/lemma normalization,
  negation mapping, a unique-modifier head-noun merge, and opt-in self-reference
  unification. Embedding/LLM synonym merging across unrelated predicate names is
  still not built, so paraphrased contradictions can be missed.

## Roadmap (matches the agreed target architecture)

1. Benchmark harness + synthetic contradiction injector (controlled hop count,
   paraphrase depth, distance), with two baselines: retrieval+NLI pairwise, and
   a frontier LLM reading the whole document. Headline metrics: false positive
   rate on clean documents, multi-hop recall, MUS localization quality.
2. NLI-based fidelity gate (deterministic verbalizer + bidirectional
   entailment with an off-the-shelf cross-encoder).
3. Embedding-based vocabulary alignment with LLM merge adjudication.
4. Chunked extraction with running context; spaCy syntactic layer feeding the
   rule translator; PDF/HTML ingestion.
5. Solver portfolio: TPTP export, Vampire fallback on `unknown`, finite model
   finding to certify satisfiability.
6. Online mode: the store is incremental by construction, so verifying an
   LLM's chain of thought step by step is this same pipeline run one
   proposition at a time.

## Project layout

```
src/schema.py           pydantic models (statements, propositions, verdicts, reports)
src/cleaning.py         deterministic cleaning + provenance spans
src/extraction.py       extraction judge + LLM translator (live and fixture providers)
src/prompts.py          live-mode prompts
src/llm_client.py       provider-agnostic client, robust JSON handling
src/vocabulary.py       canonical predicate/constant registry + FOL normalization
src/rule_translator.py  deterministic NL->FOL for a controlled fragment
src/gate.py             hybrid translation gate (equivalence + fidelity routing)
src/fol_parser.py       FOL string -> Z3, equivalence checking
src/verbalizer.py       deterministic FOL -> English
src/fidelity.py         fidelity check (v1 heuristic; NLI swap-in point)
src/solver.py           clustering, consistency, entailment, minimal conflict sets
src/report.py           Markdown + JSON reports
src/tree_builder.py     theory tree (ASCII), graph.dot, graph.svg, optional graph.png
src/main.py             CLI
tests/test_all.py       core tests, including end-to-end
tests/test_v03_features.py  negation mapping, bridges, effort, screener, timing
tests/test_v04_features.py  lemmatization, splitting, symmetric consistency, effort 3
tests/test_v05_fixes.py     false-negative regression guards (fidelity, canonicalization, graph)
tests/test_v06_features.py  extraction robustness, instance retyping, provider picker
src/normalize.py        robust extraction parsing + instance retyping
src/providers.py        provider/model picker (flags, menu, .env fallback)
providers.json          endpoint + model registry
src/lemmatizer.py       morphological merging (Tax/Taxation)
src/splitter.py         deterministic compound-statement splitting
src/timing.py           per-stage wall-clock instrumentation
src/screener.py         surface screener (lexical placeholder for NLI)
examples/               sample essay + fixtures
```
