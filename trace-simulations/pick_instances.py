#!/usr/bin/env python3
"""Pick SWE-bench Verified instances by difficulty tier.

SWE-bench Verified ships an expert-annotated ``difficulty`` field with four buckets:

    "<15 min fix"      -> easy
    "15 min - 1 hour"  -> medium
    "1-4 hours"        -> hard
    ">4 hours"         -> extremely hard

This script loads the dataset and prints candidate instance IDs for each label so you
don't have to trust a hardcoded list. By default it prints one example per label (one
each for <15 min fix, 15 min - 1 hour, 1-4 hours, and >4 hours) that are good for a
human-evaluation study: popular repos, clear issues.

Usage:
    python pick_instances.py                      # one id per difficulty label
    python pick_instances.py --per-tier 5         # 5 candidates per label
    python pick_instances.py --tier ">4 hours"    # list a single label
    python pick_instances.py --count-only         # just show how many are in each label
"""

from __future__ import annotations

import argparse
import json

DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"

# dataset label -> human-friendly difficulty name
TIERS = {
    "<15 min fix": "easy",
    "15 min - 1 hour": "medium",
    "1-4 hours": "hard",
    ">4 hours": "extremely hard",
}

# Known-good, human-readable instances per tier (sensible defaults for the study).
# These are only used as a *preference* ordering; the script still verifies they
# actually exist in the loaded dataset before recommending them.
PREFERRED = {
    "<15 min fix": ["django__django-11099", "scikit-learn__scikit-learn-13496"],
    "15 min - 1 hour": ["astropy__astropy-14995", "django__django-14238"],
    "1-4 hours": ["django__django-13401", "sympy__sympy-16988"],
    ">4 hours": ["sympy__sympy-18835", "django__django-11179", "matplotlib__matplotlib-24149"],
}


def load_instances():
    from datasets import load_dataset

    return list(load_dataset(DATASET, split=SPLIT))


def by_tier(instances):
    buckets: dict[str, list[dict]] = {label: [] for label in TIERS}
    for inst in instances:
        label = inst.get("difficulty")
        if label in buckets:
            buckets[label].append(inst)
    return buckets


def choose(buckets, label, n):
    pool = buckets.get(label, [])
    ids = {inst["instance_id"] for inst in pool}
    picked = [pid for pid in PREFERRED.get(label, []) if pid in ids]
    for inst in sorted(pool, key=lambda x: x["instance_id"]):
        if len(picked) >= n:
            break
        if inst["instance_id"] not in picked:
            picked.append(inst["instance_id"])
    return picked[:n]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-tier", type=int, default=1, help="How many candidate ids to show per tier")
    ap.add_argument("--tier", choices=list(TIERS), help="Only show this difficulty tier")
    ap.add_argument("--count-only", action="store_true", help="Only print tier counts")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = ap.parse_args()

    instances = load_instances()
    buckets = by_tier(instances)

    if args.count_only:
        counts = {label: len(items) for label, items in buckets.items()}
        print(json.dumps(counts, indent=2) if args.json else counts)
        return

    labels = [args.tier] if args.tier else ["<15 min fix", "15 min - 1 hour", "1-4 hours", ">4 hours"]
    result = {label: choose(buckets, label, args.per_tier) for label in labels}

    if args.json:
        print(json.dumps(result, indent=2))
        return

    for label in labels:
        print(f"\n[{TIERS[label]}]  difficulty = {label!r}  ({len(buckets[label])} total)")
        for pid in result[label]:
            print(f"  {pid}")
    # a ready-to-paste regex for mini-SWE-agent's --filter
    flat = [pid for ids in result.values() for pid in ids]
    print("\n--filter regex:")
    print("  " + "|".join(flat))


if __name__ == "__main__":
    main()
