"""Ablation: how much of the result comes from the LLM translator?

Re-runs the gate, vocabulary alignment and solver over statements that were
ALREADY extracted in a previous evaluation, but supplies only the deterministic
rule translator's candidate and withholds the LLM's. Everything downstream of
translation is unchanged, so the difference between this and the full run is
attributable to the translation stage alone.

This costs nothing and needs no network: the extracted statements are read from
the store.json files a previous run wrote. Note the honest scope of the claim --
extraction is still LLM work, inherited from that earlier run. This measures the
contribution of the LLM TRANSLATOR, not of the language model overall. A genuinely
LLM-free pipeline would also need deterministic extraction, which does not exist.

Usage:
    python -m tools.ablate_deterministic --set validation/proofwriter \
        --from results/eval_validation_pw_gptoss120b_primary --allow-conditionals
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from consistency_checker.gate import run_gate
from consistency_checker.normalize import strip_dangling_guards
from consistency_checker.schema import ExtractedStatement, GateOutcome, StatementType
from consistency_checker.solver import mark_duplicate_fols, verify
from consistency_checker.vocabulary import Vocabulary


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def rule_only(store: list[dict], allow_relations: bool, allow_conditionals: bool,
              guard_deontic: bool, unify_self_ref: bool) -> int:
    """Return 1 if a contradiction is found using rule translation only."""
    stmts = []
    for s in store:
        try:
            stmts.append(ExtractedStatement(
                id=s["id"], type=StatementType(s["type"]),
                original_text=s.get("original_text") or s.get("decontextualized", ""),
                decontextualized=s.get("decontextualized", ""),
                speaker=s.get("speaker", "author"),
                depends_on=s.get("depends_on", []) or []))
        except Exception:
            continue

    vocab = Vocabulary()
    props = []
    for st in stmts:
        # llm_fol=None is the ablation: only rule_translate can supply a candidate.
        props.append(run_gate(st, None, vocab, judge=None,
                              guard_deontic=guard_deontic,
                              allow_relations=allow_relations))

    aliases = vocab.finalize_modifier_aliases()
    const_aliases = vocab.finalize_self_reference_aliases() if unify_self_ref else {}
    for p in props:
        if p.fol:
            if aliases:
                p.fol = vocab.apply_pred_aliases(p.fol, aliases)
            if const_aliases:
                p.fol = vocab.apply_const_aliases(p.fol, const_aliases)
    rel, _ = vocab.finalize_relation_synonym_aliases([p.fol for p in props if p.fol])
    if rel:
        for p in props:
            if p.fol:
                p.fol = vocab.apply_relation_synonym_aliases(p.fol, rel)
    strip_dangling_guards(props)
    mark_duplicate_fols(props)

    clusters = verify(props, timeout_ms=8000, effort=1)
    accepted = sum(1 for p in props if p.status == GateOutcome.ACCEPTED and p.fol)
    found = 1 if any(c.axioms_consistent is False for c in clusters) else 0
    return found, accepted, len(props)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="set_dir", required=True)
    ap.add_argument("--from", dest="src", required=True,
                    help="a previous eval dir whose store.json files supply the statements")
    ap.add_argument("--allow-relations", action="store_true")
    ap.add_argument("--allow-conditionals", action="store_true")
    ap.add_argument("--guard-deontic", action="store_true")
    ap.add_argument("--unify-self-ref", action="store_true")
    a = ap.parse_args(argv)

    gold = json.loads((Path(a.set_dir) / "gold.json").read_text())
    docs = gold["documents"]
    src = Path(a.src)

    tp = fp = fn = tn = missing = 0
    acc_tot = stmt_tot = 0
    strata: dict[str, dict] = {}
    for d in docs:
        sj = src / d["id"] / "store.json"
        if not sj.exists():
            missing += 1
            continue
        try:
            store = json.loads(sj.read_text())
            found, acc, n = rule_only(store, a.allow_relations, a.allow_conditionals,
                                      a.guard_deontic, a.unify_self_ref)
        except Exception:
            missing += 1
            continue
        acc_tot += acc
        stmt_tot += n
        want = d["expect_inconsistent"]
        if want and found: tp += 1
        elif want and not found: fn += 1
        elif not want and found: fp += 1
        else: tn += 1
        if want and "hops" in d:
            s = strata.setdefault(str(d["hops"]), {"k": 0, "n": 0})
            s["n"] += 1
            s["k"] += found

    rec = wilson(tp, tp + fn)
    fpr = wilson(fp, fp + tn)
    pre = wilson(tp, tp + fp) if (tp + fp) else (float("nan"),) * 3
    out = {
        "ablation": "rule-translation only; LLM translator withheld",
        "note": ("extraction was inherited from the source run, so this isolates the "
                 "TRANSLATOR, not the language model overall"),
        "set": Path(a.set_dir).name, "source_run": str(src),
        "n_scored": tp + fp + fn + tn, "n_missing": missing,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "recall": {"value": rec[0], "ci95": list(rec[1:]), "n": tp + fn},
        "false_positive_rate": {"value": fpr[0], "ci95": list(fpr[1:]), "n": fp + tn},
        "precision": {"value": pre[0], "ci95": list(pre[1:]), "n": tp + fp},
        "accepted_statement_share": round(acc_tot / stmt_tot, 4) if stmt_tot else None,
        "recall_by_hops": {k: {"value": wilson(v["k"], v["n"])[0], "n": v["n"]}
                           for k, v in sorted(strata.items())},
    }
    dest = src.parent / f"{src.name}__ruleonly"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "metrics.json").write_text(json.dumps(out, indent=2))

    print(f"\n{'='*66}\nABLATION (rule translation only) | {Path(a.set_dir).name}\n{'='*66}")
    print(f"  scored {out['n_scored']}  (missing {missing})")
    for k in ("recall", "false_positive_rate", "precision"):
        v = out[k]
        s = "  n/a" if v["n"] == 0 or v["value"] != v["value"] else \
            f"{v['value']*100:5.1f}%  [{v['ci95'][0]*100:.1f}-{v['ci95'][1]*100:.1f}]  n={v['n']}"
        print(f"  {k:22} {s}")
    print(f"  statements accepted    {out['accepted_statement_share']}")
    print(f"\n  -> {dest/'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
