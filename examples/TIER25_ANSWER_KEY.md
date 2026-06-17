# Tier 2.5 Answer Key (chunking + resume stress tests)

These synthetic texts are long enough to force the chunker to split them into
multiple chunks at the default 1500-char budget. Their purpose is to prove that
**chunking does not break contradiction detection** — every planted contradiction
spans a chunk boundary, so it can only be found if all chunks' statements are
pooled into one solver run (which is the design).

Unlike real Tier 3, these have known ground truth so chunking can be verified.

## t25a_long_consistent  (3 chunks)
**Expected: NO contradiction.** A long, dense, internally-consistent account of a
provincial government. Tests that chunking a long document does not introduce a
FALSE contradiction across chunk seams. Corin is a magistrate, must publish
decisions, and does — consistently. Watch for: zero inconsistent sets.

## t25b_crosschunk_contradiction  (2 chunks)
**Expected: ONE contradiction, spanning chunks.**
- Chunk 0 establishes: every magistrate must publish decisions; every officer is
  bound by oath; magistrates are officers; **Corin is a magistrate**.
- Chunk 1 (separate chunk) states: **Corin does not publish their decisions.**
The contradiction (Corin must publish [derived] vs. Corin does not publish) is
only detectable if chunk-0 and chunk-1 statements share one solver run. This is
the core chunking-preservation test. Expected minimal set includes the publish
rule chain + Corin-is-magistrate + Corin-does-not-publish.

## t25c_crosschunk_multihop  (2 chunks)
**Expected: ONE multi-hop contradiction, spanning chunks.**
- Chunk 0 establishes the chain: every laureate is a fellow; every fellow is an
  associate; every associate has voting rights.
- Chunk 0/1 boundary: **Aldous is a laureate.**
- Chunk 1: **Aldous does not have voting rights.**
This is a 3-hop contradiction (laureate → fellow → associate → voting rights, vs.
no voting rights) that ALSO crosses a chunk boundary. Tests that multi-hop
detection — the competitive-advantage result — survives chunking. The surface
screener should stay silent (the contradiction is not lexically adjacent);
the solver should find it.

## Resume test (manual)
Run any of these with a live provider, interrupt mid-run (Ctrl-C after the first
chunk's "extracting..." line), then rerun with `--resume <out_folder>`. The
completed chunks should report "resumed from cache" and only the unfinished
chunks should call the LLM again. The final verdict must be identical to an
uninterrupted run.
