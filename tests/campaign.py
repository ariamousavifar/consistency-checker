"""Campaign test harness — runs the giant pre-release test suite as named tests.

Each *test* (deterministic, seed, ab_relations, ...) is the unit of work: it owns
a subfolder under the campaign dir, runs one or more pipeline invocations, asserts
its property, and writes a _result.json verdict. Per-example runs keep the SAME
documents you get from a normal run (report.md, store.json, timing.json, graph.*).

Layout:
    results/campaign_<stamp>/
        <test_name>/
            <example_name>/      report.md, store.json, timing.json, graph.svg, run.log, _done.json
            _result.json         PASS/FAIL for this test
        campaign_summary.txt

Invocation:
    python -m tests.campaign --all --provider cerebras2 --model gpt-oss-120b
    python -m tests.campaign --tests deterministic,seed --provider cerebras2 --model gpt-oss-120b
    python -m tests.campaign --tests correctness --examples tier1 --provider cerebras2 --model gpt-oss-120b
    python -m tests.campaign --all --provider cerebras,cerebras2     # split tests across both accounts (round-robin)
    python -m tests.campaign --resume results/campaign_20260628_0130 ...   # continue where it stopped

Resume is APPEND-ONLY: a test with a _result.json and an example with a _done.json
are never redone. (Provider-failover / true parallel execution is a future layer;
this harness is built resumable so that layer can stand on it.)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
RESULTS = ROOT / "results"
EXAMPLES = json.loads((ROOT / "examples" / "examples.json").read_text())["examples"]
PROVIDERS_CFG = json.loads((ROOT / "providers.json").read_text())["providers"]

# Applied to every live run unless a test overrides (matches the giant-test commands).
DEFAULT_ENV = {"LLM_EXTRACTION_EFFORT": "low", "LLM_TRANSLATION_EFFORT": "medium"}


# ----------------------------------------------------------------------------- helpers
def by_name(name: str) -> dict | None:
    return next((e for e in EXAMPLES if e.get("name") == name), None)


def resolve_examples(selector: str) -> list[dict]:
    """selector: all | tier1 | tier2 | tier2.5/tier25 | tier3 | <name-or-file substring>."""
    sel = (selector or "all").strip().lower()
    if sel in ("all", ""):
        return EXAMPLES
    tiers = {"tier1": 1, "tier2": 2, "tier2.5": 25, "tier25": 25, "tier3": 3}
    if sel in tiers:
        return [e for e in EXAMPLES if e.get("tier") == tiers[sel]]
    hits = [e for e in EXAMPLES
            if sel in e.get("name", "").lower() or sel in e.get("file", "").lower()]
    return hits


def ex_flags(ex: dict) -> list[str]:
    """Per-example CLI flags from examples.json (the N20 flags + bridges)."""
    out: list[str] = []
    fl = ex.get("flags", {}) or {}
    if fl.get("allow_relations"):
        out += ["--allow-relations"]
    if fl.get("allow_conditionals"):
        out += ["--allow-conditionals"]
    if fl.get("guard_deontic"):
        out += ["--guard-deontic"]
    if fl.get("unify_self_ref"):
        out += ["--unify-self-ref"]
    if ex.get("bridges"):
        out += ["--bridges", ex["bridges"]]
    return out


def has_key(provider: str) -> bool:
    cfg = PROVIDERS_CFG.get(provider, {})
    return any(os.environ.get(env) for env in cfg.get("api_key_env", []))


def _report(out_dir: Path) -> dict | None:
    rj = out_dir / "report.json"
    if not rj.exists():
        return None
    try:
        return json.loads(rj.read_text())
    except Exception:
        return None


def count_inconsistent(out_dir: Path) -> int | None:
    d = _report(out_dir)
    if d is None:
        return None
    return sum(1 for cl in d.get("clusters", []) if not cl.get("axioms_consistent", True))


def refutation_nodes(out_dir: Path) -> int | None:
    d = _report(out_dir)
    if d is None:
        return None
    n = 0
    for cl in d.get("clusters", []):
        ref = cl.get("refutation")
        if ref:
            n += len(ref.get("nodes", {}))
    return n


def pipeline_ms(out_dir: Path) -> float | None:
    d = _report(out_dir)
    if d is None:
        return None
    return round(sum(t.get("seconds", 0) for t in d.get("timing", [])) * 1000, 1)


def usage(out_dir: Path) -> dict:
    d = _report(out_dir) or {}
    return d.get("usage", {})


# ----------------------------------------------------------------------------- one run
def do_run(out_dir: Path, *, file: str | None = None, extra: list[str] | None = None,
           env: dict | None = None, provider: str | None = None, model: str | None = None,
           seed: int | None = None, offline: bool = False, timeout: int = 1800) -> dict:
    """One `python -m src.main ... --out out_dir` invocation. Append-only: a dir
    with _done.json is returned as-is (resume). Returns parsed metrics."""
    out_dir = Path(out_dir)
    done = out_dir / "_done.json"
    if done.exists():
        info = json.loads(done.read_text())
        info["resumed"] = True
        return info
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [PY, "-m", "src.main", "--out", str(out_dir)]
    if offline:
        cmd += ["--offline"]
    if file:
        cmd += ["--file", file]
    if not offline:
        if provider:
            cmd += ["--provider", provider]
        if model:
            cmd += ["--model", model]
        if seed is not None:
            cmd += ["--seed", str(seed)]
    if extra:
        cmd += list(extra)

    runenv = dict(os.environ)
    runenv.update(DEFAULT_ENV)
    if env:
        runenv.update(env)

    t0 = time.time()
    timed_out, rc = False, None
    with open(out_dir / "run.log", "w") as lf:
        try:
            rc = subprocess.run(cmd, cwd=ROOT, env=runenv, stdout=lf,
                                stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            lf.write(f"\n[campaign] TIMEOUT after {timeout}s\n")

    info = {
        "cmd": " ".join(cmd),
        "provider": provider, "env": env or {},
        "returncode": rc, "timed_out": timed_out,
        "wall_s": round(time.time() - t0, 1),
        "inconsistent": count_inconsistent(out_dir),
        "usage": usage(out_dir),
        "pipeline_ms": pipeline_ms(out_dir),
    }
    if rc == 0 and not timed_out:
        done.write_text(json.dumps(info, indent=2))   # checkpoint: never redone on resume
    return info


def log_has(out_dir: Path, needle: str) -> bool:
    log = Path(out_dir) / "run.log"
    return log.exists() and needle in log.read_text(errors="ignore")


# ----------------------------------------------------------------------------- result
def result(td: Path, name: str, provider, status: str, rows, notes: str = "") -> dict:
    res = {"test": name, "provider": provider, "status": status, "rows": rows, "notes": notes}
    (td / "_result.json").write_text(json.dumps(res, indent=2))
    return res


# ----------------------------------------------------------------------------- tests
# Each test: fn(td, prov, ctx) -> result dict.  td = campaign_dir/<test>, prov = assigned provider.

def t_unit(td, prov, ctx):
    td.mkdir(parents=True, exist_ok=True)
    log = td / "pytest.log"
    with open(log, "w") as lf:
        rc = subprocess.run([PY, "-m", "pytest", "-q"], cwd=ROOT,
                            stdout=lf, stderr=subprocess.STDOUT).returncode
    tail = log.read_text().strip().splitlines()[-1:] if log.exists() else [""]
    return result(td, "unit", None, "pass" if rc == 0 else "fail",
                  [{"pytest_rc": rc, "tail": tail[0] if tail else ""}])


def t_offline(td, prov, ctx):
    ex = by_name("sample_essay")
    od = td / "sample_essay"
    info = do_run(od, file=ex["file"], offline=True, timeout=ctx.timeout)
    inc = info["inconsistent"]
    d = _report(od) or {}
    has_mortal = any("Mortal" in (cl.get("refutation", {}) or {}).get("text", "")
                     for cl in d.get("clusters", []))
    ok = info["returncode"] == 0 and inc == 1 and has_mortal
    return result(td, "offline", None, "pass" if ok else "fail",
                  [{"example": "sample_essay", "inconsistent": inc, "mortal_refutation": has_mortal}],
                  "expect 1 inconsistent set with Mortal vs not Mortal")


def t_correctness(td, prov, ctx):
    rows, ok = [], True
    for ex in resolve_examples(ctx.examples):
        info = do_run(td / ex["name"], file=ex["file"], extra=ex_flags(ex),
                      provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
        rows.append({"example": ex["name"], "inconsistent": info["inconsistent"],
                     "rc": info["returncode"], "timed_out": info["timed_out"], "wall_s": info["wall_s"]})
        ok = ok and info["returncode"] == 0 and not info["timed_out"]
    return result(td, "correctness", prov, "pass" if ok else "fail", rows,
                  "record-only: review inconsistent counts; PASS = all completed")


def t_performance(td, prov, ctx):
    targets = [e for e in resolve_examples(ctx.examples)
               if e["name"] in ("wikipedia2_neptune", "stanford_theology")] or \
              resolve_examples(ctx.examples)[:2]
    rows, ok = [], True
    for ex in targets:
        info = do_run(td / ex["name"], file=ex["file"], extra=ex_flags(ex),
                      provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
        rows.append({"example": ex["name"], "pipeline_ms": info["pipeline_ms"],
                     "tokens": info["usage"].get("total_tokens"), "calls": info["usage"].get("calls"),
                     "wall_s": info["wall_s"], "rc": info["returncode"], "timed_out": info["timed_out"]})
        ok = ok and info["returncode"] == 0 and not info["timed_out"]
    return result(td, "performance", prov, "pass" if ok else "fail", rows,
                  "record-only: timing/tokens for heavy docs")


def t_ab_relations(td, prov, ctx):
    sel = ctx.examples if ctx.examples != "all" else None
    exs = resolve_examples(sel) if sel else [e for e in EXAMPLES if e.get("tier") in (1, 2)]
    rows, ok = [], True
    for ex in exs:
        base = do_run(td / f"{ex['name']}__base", file=ex["file"], extra=ex_flags(ex),
                      provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
        rel = do_run(td / f"{ex['name']}__relations", file=ex["file"],
                     extra=ex_flags(ex) + ["--allow-relations"],
                     provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
        same = base["inconsistent"] == rel["inconsistent"]
        rows.append({"example": ex["name"], "base": base["inconsistent"],
                     "relations": rel["inconsistent"], "identical": same})
        ok = ok and same and base["returncode"] == 0 and rel["returncode"] == 0
    return result(td, "ab_relations", prov, "pass" if ok else "fail", rows,
                  "verdicts must be identical with/without --allow-relations (no new FPs)")


def t_fragment_flags(td, prov, ctx):
    # bush RECLASSIFIED to expect 0: modal political speech ("will not raise
    # taxes") correctly quarantines -- a broken promise across time is hypocrisy,
    # not P-and-not-P; the bridge mechanism is tested non-modally by taxation.
    specs = [
        ("rothbard", "t3_rothbard_selfownership", ["--allow-conditionals", "--guard-deontic"], 0),
        ("bush", "t3_bush_1988_notaxes", ["--unify-self-ref"], 0),
        ("taxation", "taxation_bridged", [], 1),
    ]
    rows, ok = [], True
    for label, name, extra, expect in specs:
        ex = by_name(name)
        if not ex:
            rows.append({"label": label, "error": "example not found"})
            ok = False
            continue
        info = do_run(td / label, file=ex["file"], extra=ex_flags(ex) + extra,
                      provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
        hit = info["inconsistent"] == expect
        rows.append({"label": label, "expected": expect, "actual": info["inconsistent"], "ok": hit})
        ok = ok and hit
    return result(td, "fragment_flags", prov, "pass" if ok else "fail", rows,
                  "Rothbard 0; bush 0 (modality out of scope by design); taxation 1 bridged")


def t_rendering(td, prov, ctx):
    ex = by_name("rel_two_branch_conflict")
    base = ["--allow-relations"]
    full = do_run(td / "full", file=ex["file"], extra=base,
                  provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    pruned = do_run(td / "pruned", file=ex["file"], extra=base + ["--prune-derivation"],
                    provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    notree = do_run(td / "notree", file=ex["file"], extra=base + ["--no-tree"],
                    provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    nf, npd = refutation_nodes(td / "full"), refutation_nodes(td / "pruned")
    prune_ok = nf is not None and npd is not None and npd <= nf
    notree_ok = not log_has(td / "notree", "theory cluster") and (td / "notree" / "report.json").exists()
    ok = prune_ok and notree_ok
    return result(td, "rendering", prov, "pass" if ok else "fail",
                  [{"check": "prune", "full_nodes": nf, "pruned_nodes": npd, "ok": prune_ok},
                   {"check": "no_tree", "console_tree_suppressed": notree_ok}],
                  "pruned derivation <= full; --no-tree hides console tree but writes files")


def t_chunking(td, prov, ctx):
    ex = by_name("t25b_crosschunk_contradiction")
    chunked = do_run(td / "chunked", file=ex["file"], extra=ex_flags(ex),
                     provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    nochunk = do_run(td / "no_chunk", file=ex["file"], extra=ex_flags(ex) + ["--no-chunk"],
                     provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    ok = chunked["returncode"] == 0 and nochunk["returncode"] == 0
    return result(td, "chunking", prov, "pass" if ok else "fail",
                  [{"mode": "chunked", "inconsistent": chunked["inconsistent"], "ms": chunked["pipeline_ms"]},
                   {"mode": "no_chunk", "inconsistent": nochunk["inconsistent"], "ms": nochunk["pipeline_ms"]}],
                  "record-only: compare chunked vs single-pass detection/timing")


def t_effort(td, prov, ctx):
    ex = by_name("rel_two_branch_conflict")
    base = ["--allow-relations"]
    rows, ok = [], True
    for e in (0, 1, 2, 3):
        extra = base + ["--effort", str(e)] + (["--solver-timeout-ms", "12000"] if e == 2 else [])
        info = do_run(td / f"effort{e}", file=ex["file"], extra=extra,
                      provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
        rows.append({"effort": e, "inconsistent": info["inconsistent"], "rc": info["returncode"]})
        if e in (2, 3):
            ok = ok and (info["inconsistent"] or 0) >= 1
        else:
            ok = ok and info["returncode"] == 0
    return result(td, "effort", prov, "pass" if ok else "fail", rows,
                  "effort 0 = screener only; effort 2/3 must still find the contradiction globally")


def t_determinism(td, prov, ctx):
    ex = by_name("rel_command_theory_tree")
    a = do_run(td / "run_a", file=ex["file"], extra=["--allow-relations"],
               provider=prov, model=ctx.model, seed=7, timeout=ctx.timeout)
    b = do_run(td / "run_b", file=ex["file"], extra=["--allow-relations"],
               provider=prov, model=ctx.model, seed=7, timeout=ctx.timeout)
    sa, sb = td / "run_a" / "store.json", td / "run_b" / "store.json"
    identical = sa.exists() and sb.exists() and sa.read_bytes() == sb.read_bytes()
    ok = a["returncode"] == 0 and b["returncode"] == 0 and identical
    return result(td, "determinism", prov, "pass" if ok else "fail",
                  [{"store_byte_identical": identical}],
                  "two seed-7 runs must produce byte-identical store.json")


def t_seed(td, prov, ctx):
    # Fixture: honor --examples when given (first resolved example), else the
    # relational theory-tree default. ex_flags() supplies its proper flags, so a
    # tier-1 example runs flag-free while rel_command_tree gets --allow-relations.
    fixture = (resolve_examples(ctx.examples)[0] if ctx.examples != "all"
               else by_name("rel_command_theory_tree"))
    extra = ex_flags(fixture)
    provs = ctx.providers                      # split this test's runs across accounts (round-robin)
    runs = (("seed7", 7), ("seed21", 21), ("noseed", -1))
    rows, infos = [], []
    for i, (label, sd) in enumerate(runs):
        p = provs[i % len(provs)]
        info = do_run(td / label, file=fixture["file"], extra=extra,
                      provider=p, model=ctx.model, seed=sd, timeout=ctx.timeout)
        infos.append(info)
        rows.append({"seed": sd, "provider": p, "inconsistent": info["inconsistent"],
                     "rc": info["returncode"]})
    counts = [i["inconsistent"] for i in infos]
    ok = all(c == counts[0] for c in counts) and all(i["returncode"] == 0 for i in infos)
    return result(td, "seed", ",".join(provs), "pass" if ok else "fail", rows,
                  f"fixture={fixture['name']}; verdict (inconsistent count) stable across "
                  "seeds and providers; runs split round-robin across accounts")


def t_provider_matrix(td, prov, ctx):
    ex = by_name("rel_authority_conflict")
    rows, counts = [], []
    for p in ctx.providers:
        if not has_key(p):
            rows.append({"provider": p, "skipped": "no api key"})
            continue
        info = do_run(td / p, file=ex["file"], extra=["--allow-relations"],
                      provider=p, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
        rows.append({"provider": p, "inconsistent": info["inconsistent"], "rc": info["returncode"]})
        if info["returncode"] == 0:
            counts.append(info["inconsistent"])
    ok = len(counts) >= 1 and all(c == counts[0] for c in counts)
    return result(td, "provider_matrix", ",".join(ctx.providers), "pass" if ok else "fail", rows,
                  "same contradiction across all available providers (skips providers w/o key)")


def t_nli(td, prov, ctx):
    ex = by_name("t25c_crosschunk_multihop_original") or by_name("t25c_crosschunk_multihop")
    base = do_run(td / "base", file=ex["file"], extra=["--allow-relations"],
                  provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    nli = do_run(td / "nli", file=ex["file"], extra=["--allow-relations", "--nli"],
                 provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    cb, cn = base["usage"].get("calls", 0), nli["usage"].get("calls", 0)
    ok = nli["returncode"] == 0 and cn >= cb
    return result(td, "nli", prov, "pass" if ok else "fail",
                  [{"base_calls": cb, "nli_calls": cn}],
                  "--nli adds adjudication calls; verdict holds")


def t_env_knobs(td, prov, ctx):
    cmd = by_name("rel_command_theory_tree")
    pre = by_name("rel_prereq_broken")
    retry0 = do_run(td / "retry0", file=pre["file"], extra=["--allow-relations"],
                    env={"LLM_TRANSLATION_RETRY": "0"},
                    provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    minint = do_run(td / "min_interval", file=cmd["file"], extra=["--allow-relations"],
                    env={"LLM_MIN_INTERVAL": "3"},
                    provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    reason = do_run(td / "reasoning_low", file=cmd["file"], extra=["--allow-relations"],
                    env={"LLM_REASONING_EFFORT": "low"},
                    provider=prov, model=ctx.model, seed=ctx.seed, timeout=ctx.timeout)
    retry_off = not log_has(td / "retry0", "[translate-retry]")
    ok = retry_off and minint["returncode"] == 0 and reason["returncode"] == 0
    return result(td, "env_knobs", prov, "pass" if ok else "fail",
                  [{"check": "RETRY=0 no retry line", "ok": retry_off},
                   {"check": "MIN_INTERVAL=3 completes", "ok": minint["returncode"] == 0},
                   {"check": "REASONING_EFFORT=low completes", "ok": reason["returncode"] == 0}],
                  "env-only knobs behave; RETRY=0 suppresses the [translate-retry] pass")


REGISTRY = {
    "unit": t_unit,
    "offline": t_offline,
    "correctness": t_correctness,
    "performance": t_performance,
    "ab_relations": t_ab_relations,
    "fragment_flags": t_fragment_flags,
    "rendering": t_rendering,
    "chunking": t_chunking,
    "effort": t_effort,
    "determinism": t_determinism,
    "seed": t_seed,
    "provider_matrix": t_provider_matrix,
    "nli": t_nli,
    "env_knobs": t_env_knobs,
}


class Ctx:
    def __init__(self, model, seed, providers, examples, timeout):
        self.model = model
        self.seed = seed
        self.providers = providers
        self.examples = examples
        self.timeout = timeout


# ----------------------------------------------------------------------------- dispatch
def main(argv=None):
    ap = argparse.ArgumentParser(description="Consistency-checker campaign test harness")
    ap.add_argument("--tests", default=None,
                    help="comma list of tests to run (default: all). Names: " + ", ".join(REGISTRY))
    ap.add_argument("--all", action="store_true", help="run every test")
    ap.add_argument("--examples", default="all",
                    help="input for input-driven tests: all|tier1|tier2|tier2.5|tier3|<name-or-file>")
    ap.add_argument("--provider", default="cerebras2",
                    help="provider, or comma list to split tests round-robin across accounts "
                         "(e.g. cerebras,cerebras2)")
    ap.add_argument("--model", default="gpt-oss-120b")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--run-timeout", type=int, default=1800, help="per-run timeout (s)")
    ap.add_argument("--resume", default=None, help="continue an existing results/campaign_... dir")
    ap.add_argument("--list", action="store_true", help="list test names and exit")
    args = ap.parse_args(argv)

    if args.list:
        print("tests:", ", ".join(REGISTRY))
        return 0

    if args.all or not args.tests:
        names = list(REGISTRY)
    else:
        names = [t.strip() for t in args.tests.split(",") if t.strip()]
        bad = [n for n in names if n not in REGISTRY]
        if bad:
            ap.error(f"unknown test(s): {bad}. Known: {list(REGISTRY)}")

    providers = [p.strip() for p in args.provider.split(",") if p.strip()]
    ctx = Ctx(args.model, args.seed, providers, args.examples, args.run_timeout)

    if args.resume:
        campaign = Path(args.resume)
        if not campaign.exists():
            ap.error(f"resume dir {campaign} not found")
        print(f"[resume] continuing {campaign}")
    else:
        campaign = RESULTS / f"campaign_{datetime.now():%Y%m%d_%H%M}"
    campaign.mkdir(parents=True, exist_ok=True)

    print(f"campaign: {campaign}")
    print(f"providers: {providers}  model: {args.model}  seed: {args.seed}  examples: {args.examples}")
    print(f"tests: {names}\n")

    summary = []
    for i, name in enumerate(names):
        td = campaign / name
        if (td / "_result.json").exists():
            res = json.loads((td / "_result.json").read_text())
            print(f"[resume] {name:16} already done -> {res['status'].upper()}")
            summary.append((name, res["status"], "(resumed)"))
            continue
        prov = providers[i % len(providers)]      # per-test provider assignment (round-robin)
        td.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        print(f"[run]    {name:16} on {prov} ...", flush=True)
        try:
            res = REGISTRY[name](td, prov, ctx)
            status = res["status"]
        except Exception as exc:
            import traceback
            (td / "_error.log").write_text(traceback.format_exc())
            res = result(td, name, prov, "error", [{"exception": str(exc)}])
            status = "error"
        dt = round(time.time() - t0)
        print(f"         {name:16} -> {status.upper()}  ({dt}s)")
        summary.append((name, status, f"{dt}s"))

    lines = ["CAMPAIGN SUMMARY", f"dir: {campaign}",
             f"providers: {providers}  model: {args.model}  seed: {args.seed}", ""]
    width = max((len(n) for n, _, _ in summary), default=8)
    for name, status, extra in summary:
        lines.append(f"  {name:<{width}}  {status.upper():6}  {extra}")
    npass = sum(1 for _, s, _ in summary if s == "pass")
    lines += ["", f"{npass}/{len(summary)} passed"]
    text = "\n".join(lines)
    (campaign / "campaign_summary.txt").write_text(text + "\n")
    print("\n" + text)
    return 0 if npass == len(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
