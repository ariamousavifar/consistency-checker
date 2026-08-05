# Architecture

This document describes what the system **actually does today**, stage by stage,
and marks clearly where the implementation is partial or where a component is
still planned. `Architecture-target.pdf` in this folder is the original design
sketch and is deliberately more ambitious than the current build — treat it as a
roadmap, not a description.

Legend used throughout: **[built]** works today · **[partial]** exists in a
reduced form · **[planned]** designed, not implemented.

---

## The pipeline

```mermaid
flowchart TD
    A[raw text<br/><i>plain .txt only</i>] --> B["1 · cleaning<br/><b>[built]</b> paragraphs, speaker turns, character offsets"]
    B --> C["2 · extraction judge<br/><b>[built · LLM]</b> claim filter, type judge, decontextualising rewriter"]

    C --> D["3a · LLM translation<br/><b>[built · LLM]</b> statement → FOL, shared growing vocabulary"]
    C --> R["3b · rule translator<br/><b>[partial]</b> 5 regex patterns, ~5% real coverage"]

    D --> E{"4 · hybrid translation gate<br/><b>[built]</b>"}
    R --> E

    E -->|"Z3 proves the two<br/>candidates equivalent"| V
    E -->|"they diverge →<br/>fidelity check picks one"| V
    E -->|"ambiguous, hedged, deontic,<br/>outside the fragment"| Q["quarantined<br/><b>[built]</b> with a written reason"]

    V["5 · vocabulary alignment<br/><b>[built]</b> canonicalisation, negation mapping,<br/>synonym + constant unification"] --> DD

    DD["6 · dedup<br/><b>[built]</b> alpha-normalised"] --> SC
    SC["7 · surface screener<br/><b>[partial]</b> lexical heuristic, not an NLI model"] --> S

    S["8 · solver — <b>Z3</b><br/><b>[built]</b> clustering · consistency · entailment<br/>minimal unsat cores · reductio"] --> FC
    FC["9 · refutation reconstruction<br/><b>[built]</b> forward chaining: how the contradiction arises"] --> H

    H["10 · reports<br/><b>[built]</b> Markdown · JSON · theory tree · graphs"]
    Q -.-> H

    style C fill:#fff3cd,stroke:#856404
    style D fill:#fff3cd,stroke:#856404
    style R fill:#e2e3e5,stroke:#6c757d
    style SC fill:#e2e3e5,stroke:#6c757d
    style V fill:#d4edda,stroke:#155724
    style DD fill:#d4edda,stroke:#155724
    style S fill:#d4edda,stroke:#155724
    style FC fill:#d4edda,stroke:#155724
    style Q fill:#f0f0f0,stroke:#888,stroke-dasharray: 4 3
```

Yellow marks the two stages where a language model is used. Green is
deterministic, auditable code. Grey marks components that exist but in a reduced
form. **The division is the central design claim: the model proposes, Z3
disposes.** No logical verdict is ever produced by a language model.

The exact runtime order is visible in `consistency_checker/pipeline.py`, where every stage is
timed:

```
read_and_clean → extraction → translation → gate → bridges
   → unify_predicates → dedup → screener → solver → write_outputs
```

---

## Stage notes

**1 · Cleaning** — deterministic. Establishes the character offsets that give
every later claim its provenance back to the source text.

**2 · Extraction judge** *(LLM)* — decides which sentences make claims, assigns a
type (`axiom`, `derived_claim`, `attributed`, `hypothetical`, `rhetorical`,
`non_propositional`), and rewrites each into a self-contained form. Long documents
are chunked, and each chunk's result is cached so an interrupted run resumes.

**3 · Dual translation** — the design's core redundancy. A deterministic rule
translator and the LLM each propose a formula for the same statement,
*independently*. The rule translator currently covers a small fragment (universals,
instances, simple existentials); growing it via dependency-parse composition rather
than regex is the main planned reduction in LLM reliance.

**4 · Hybrid translation gate** — arbitration. If Z3 proves the two candidates
logically equivalent, confidence is high. If they diverge, a fidelity check decides
which (if either) faithfully reflects the sentence. Anything hedged, deontic,
modal, or outside the decidable fragment is quarantined **with a recorded reason**
rather than dropped. Nothing unverified reaches the solver.

**5 · Vocabulary alignment** — the layer that makes contradictions *findable*.
Two statements only clash in Z3 if they share symbols, so this canonicalises
morphology, maps negated predicates onto their base (`Immortal` → `not Mortal`),
merges modifier variants, unifies inconsistent spellings of the same constant, and
merges curated relational synonyms with argument direction (`Prerequisite(a,b)` ≡
`Require(b,a)`). Every rewrite is recorded in the report — the merge is a lens for
the solver, never a silent edit of the author's text.

Note that vocabulary is *threaded through* the pipeline rather than being one
discrete step: the registry is created before translation, consulted during the
gate, and finalised afterwards — because the synonym merge can only run once every
predicate in the document has been seen.

**6 · Dedup** — over-extraction produces near-duplicate statements whose logic is
identical up to bound-variable naming. These collapse to one canonical node so
duplication cannot manufacture spurious derivation edges.

**7 · Surface screener** — *partial.* A lexical heuristic that flags superficially
opposed sentence pairs. It stands in the position the design reserves for a real
NLI model. Its output is informational and never affects a verdict.

**8 · Solver** — Z3. Clusters propositions by shared predicates so one bad cluster
cannot poison unrelated statements; checks each cluster for consistency; shrinks
any unsatisfiable core to a *minimal* conflicting set; establishes entailments in
layers so theorems can rest on earlier theorems, producing a derivation tree rather
than a flat fan; and treats a hypothetical as a reductio assumption, so refuting it
proves its negation instead of reading as self-contradiction.

**9 · Refutation reconstruction** — Z3 can prove a set inconsistent but cannot show
*how*, because everything follows from a contradiction. A constructive forward
chainer re-derives the two colliding chains, so the report shows the reasoning that
produced the conflict. Limited to the Horn fragment; existential contradictions
still report a minimal set without a derivation.

**10 · Reports** — Markdown, JSON, an ASCII theory tree, and graphs, all carrying
source offsets.

---

## Planned, not built

Named here so the diagram is not read as a promise:

- **Dependency parser and coreference resolution** — the syntactic layer in the
  original design. Decontextualisation is currently handled inside the extraction
  prompt instead.
- **NLI-based fidelity checking** — the fidelity gate is lexical coverage today.
  A live experiment found a general NLI check over-quarantined faithful
  translations and slowed the gate substantially, so it was narrowed to
  adjudicating genuine two-candidate divergences only.
- **Format readers** for PDF, HTML, and transcripts.
- **Solver portfolio** — TPTP export and a Vampire fallback when Z3 returns
  `unknown`.
- **Per-speaker belief sets** — currently one asserted belief set per run;
  attributed statements are excluded with a reason.
