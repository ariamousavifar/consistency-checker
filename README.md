# Internal-Inconsistency Checker (prototype v0.4)

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
4. Five-valued verdicts: entailed / not_entailed / contradicts / unknown /
   error. Solver timeouts map to `unknown`, never misreported.
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
   All 54 tests run offline in about a second.

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
- Vocabulary alignment is deterministic plural/case normalization; synonym and
  paraphrase merging (embedding clustering + LLM adjudication) is not built,
  so paraphrased contradictions across different predicate names are missed.
- The rule translator covers a small controlled fragment on purpose
  (precision-first; it refuses the rest).
- Plain-text input only; PDF/HTML cleaning modules are planned behind the same
  interface.
- FOL cannot express causation, modality, tense, generics, or comparatives;
  the translator is instructed to return null for those, and the gate
  quarantines them.
- Single-speaker belief sets per run; `attributed` and `hypothetical`
  statements are excluded with a recorded reason rather than modeled.

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
src/lemmatizer.py       morphological merging (Tax/Taxation)
src/splitter.py         deterministic compound-statement splitting
src/timing.py           per-stage wall-clock instrumentation
src/screener.py         surface screener (lexical placeholder for NLI)
examples/               sample essay + fixtures
```
