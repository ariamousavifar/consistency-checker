"""Generate a HELD-OUT validation set with ground truth known by construction.

Why generated rather than scraped: the labels are not a judgement call. A
document is built by planting a subsumption chain of known length and then
either closing it with a contradicting instance or not, so the correct answer --
and the exact set of statements responsible -- is known before any system sees
the text. That removes both label noise and the circularity of the authors of a
system also deciding what its answers should be.

Why held out: every example in examples/ was used while developing the system;
fixes were made in response to failures on them. Numbers measured there describe
memorisation, not generalisation. This set uses disjoint vocabulary domains and
is generated from a recorded seed, so it can be regenerated but was never tuned
against.

Controlled variables, one per document:
  * hop distance 1-5   how many implications separate the planted instance from
                       the statement it contradicts. This is the axis on which
                       sentence-pair methods fail by construction: at hop >= 2 no
                       PAIR of sentences is inconsistent.
  * consistent control the same document with the contradiction removed, which
                       is what measures the false-positive rate.
  * distractors        unrelated true sentences, so detection cannot be a
                       side-effect of short input.
  * surface form       four phrasings per rule, so a result is not an artefact of
                       one template.

Usage:
    python -m tools.make_validation_set --out validation --n 120 --seed 20260816
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

# Vocabulary domains chosen to be DISJOINT from everything in examples/: no
# philosophers, taxation, contracts, whales, geometry, witnesses, citizens,
# subscribers, cats, ants, rivers, primes, courses, genealogy, or academies.
DOMAINS = [
    ("aviation", ["turboprop", "aircraft", "registered_craft", "insured_asset",
                  "audited_item", "tracked_object"], ["flightworthy", "grounded"],
     ["The tower logs every departure.", "Fuel is measured in litres."]),
    ("pharmacy", ["antibiotic", "prescription_drug", "regulated_substance",
                  "logged_item", "audited_stock", "traced_product"],
     ["dispensable", "recalled"],
     ["The pharmacy opens at eight.", "Labels are printed in duplicate."]),
    ("banking", ["savings_account", "deposit_account", "insured_account",
                 "reported_account", "reviewed_account", "flagged_account"],
     ["interest_bearing", "frozen"],
     ["Statements are issued monthly.", "The branch has two counters."]),
    ("shipping", ["reefer_container", "sealed_container", "manifested_cargo",
                  "customs_item", "bonded_good", "scanned_unit"],
     ["temperature_controlled", "quarantined"],
     ["The port operates two cranes.", "Manifests are filed in triplicate."]),
    ("library", ["reference_volume", "catalogued_book", "archived_work",
                 "insured_holding", "restricted_item", "recorded_asset"],
     ["consultable", "withdrawn"],
     ["The reading room seats forty.", "Catalogue cards are alphabetical."]),
    ("employment", ["apprentice", "trainee", "salaried_worker", "insured_employee",
                    "registered_staff", "payrolled_person"],
     ["pension_eligible", "suspended"],
     ["The office closes on Sundays.", "Timesheets are weekly."]),
    ("chemistry", ["alkane", "hydrocarbon", "organic_compound", "catalogued_reagent",
                   "controlled_reagent", "inventoried_chemical"],
     ["combustible", "inert"],
     ["The lab has four fume hoods.", "Reagents are stored by class."]),
    ("insurance", ["motor_policy", "vehicle_policy", "active_policy",
                   "underwritten_policy", "reinsured_policy", "audited_policy"],
     ["claimable", "voided"],
     ["Premiums are billed quarterly.", "Adjusters work in pairs."]),
    ("municipal", ["allotment", "public_plot", "registered_land",
                   "surveyed_parcel", "rated_property", "recorded_holding"],
     ["leasable", "condemned"],
     ["The council meets on Thursdays.", "Surveys use metric units."]),
    ("broadcasting", ["shortwave_station", "licensed_station", "regulated_service",
                      "monitored_service", "logged_service", "audited_service"],
     ["transmittable", "off_air"],
     ["The mast is forty metres tall.", "Logs are kept for a year."]),
]

# Four surface forms per universal rule, so a measured result is not an artefact
# of a single phrasing.
RULE_FORMS = [
    "Every {a} is a {b}.",
    "All {a}s are {b}s.",
    "Any {a} is also a {b}.",
    "If something is a {a} then it is a {b}.",
]
PROP_FORMS = [
    "Every {b} is {p}.",
    "All {b}s are {p}.",
    "Any {b} is {p}.",
    "If something is a {b} then it is {p}.",
]
INSTANCE_FORMS = ["{n} is a {a}.", "The item {n} is a {a}.", "{n} counts as a {a}."]
NEG_FORMS = ["{n} is not {p}.", "It is not the case that {n} is {p}.",
             "{n} fails to be {p}."]

NAMES = ["unit_alpha", "unit_bravo", "unit_charlie", "unit_delta", "unit_echo",
         "unit_foxtrot", "unit_golf", "unit_hotel", "unit_india", "unit_juliet"]


def _words(token: str) -> str:
    return token.replace("_", " ")


def build_document(rng: random.Random, hops: int, contradictory: bool,
                   n_distractors: int, doc_id: str) -> dict:
    """One validation document plus its ground truth.

    A chain of `hops` implications leads from a class to a property; an instance
    is asserted in the first class; when `contradictory`, the property is then
    denied of that instance. The contradiction therefore requires exactly `hops`
    implication steps to surface, and NO PAIR of sentences is inconsistent once
    hops >= 2.
    """
    domain, classes, props, distractors = DOMAINS[rng.randrange(len(DOMAINS))]
    chain = classes[: hops + 1]
    prop = props[0]
    name = NAMES[rng.randrange(len(NAMES))]

    sentences: list[str] = []
    gold_ids: list[str] = []      # sentence indices that form the minimal set

    # subsumption chain: c0 -> c1 -> ... -> c_hops
    for i in range(hops):
        form = RULE_FORMS[rng.randrange(len(RULE_FORMS))]
        sentences.append(form.format(a=_words(chain[i]), b=_words(chain[i + 1])))
        gold_ids.append(str(len(sentences)))
    # final class carries the property
    sentences.append(PROP_FORMS[rng.randrange(len(PROP_FORMS))]
                     .format(b=_words(chain[hops]), p=_words(prop)))
    gold_ids.append(str(len(sentences)))
    # the instance
    sentences.append(INSTANCE_FORMS[rng.randrange(len(INSTANCE_FORMS))]
                     .format(n=_words(name), a=_words(chain[0])))
    gold_ids.append(str(len(sentences)))

    if contradictory:
        sentences.append(NEG_FORMS[rng.randrange(len(NEG_FORMS))]
                         .format(n=_words(name), p=_words(prop)))
        gold_ids.append(str(len(sentences)))
    else:
        # Consistent control: assert the property of a DIFFERENT entity, so the
        # document is the same length and style but entails no conflict.
        other = NAMES[(NAMES.index(name) + 1) % len(NAMES)]
        sentences.append(f"{_words(other)} is not {_words(prop)}.")
        gold_ids = []

    for _ in range(n_distractors):
        sentences.append(distractors[rng.randrange(len(distractors))])

    body = " ".join(sentences[: hops + 2])
    tail = " ".join(sentences[hops + 2:])
    text = f"Notes on {domain}\n\n{body}\n\n{tail}\n"

    return {
        "id": doc_id,
        "domain": domain,
        "hops": hops,
        "expect_inconsistent": 1 if contradictory else 0,
        "gold_sentence_count": len(sentences),
        "gold_minimal_set_size": len(gold_ids) if contradictory else 0,
        "text": text,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="validation")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20260816)
    a = ap.parse_args(argv)

    rng = random.Random(a.seed)
    out = Path(a.out)
    (out / "docs").mkdir(parents=True, exist_ok=True)

    manifest = []
    # Balanced by construction: half contradictory, half clean, hops spread 1-5.
    per_cell = max(1, a.n // 10)
    i = 0
    for hops in (1, 2, 3, 4, 5):
        for contradictory in (True, False):
            for _ in range(per_cell):
                i += 1
                doc_id = f"v{i:03d}_h{hops}_{'pos' if contradictory else 'neg'}"
                d = build_document(rng, hops, contradictory,
                                   n_distractors=rng.randrange(0, 3), doc_id=doc_id)
                (out / "docs" / f"{doc_id}.txt").write_text(d.pop("text"), encoding="utf-8")
                manifest.append(d)

    (out / "gold.json").write_text(json.dumps({
        "seed": a.seed,
        "generator": "tools/make_validation_set.py",
        "note": ("Held-out validation set. Labels are known by construction, not "
                 "assigned by inspection. Vocabulary domains are disjoint from "
                 "examples/, which was the development set."),
        "documents": manifest,
    }, indent=2), encoding="utf-8")

    pos = sum(d["expect_inconsistent"] for d in manifest)
    print(f"{len(manifest)} documents -> {out}/  ({pos} contradictory, "
          f"{len(manifest)-pos} clean, hops 1-5, seed {a.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
