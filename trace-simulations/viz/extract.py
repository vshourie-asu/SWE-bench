#!/usr/bin/env python3
"""Base_Commit_Extractor: per-task base commit and metadata from SWE-bench Verified.

This module implements Requirement 1 of the SWE-bench change-visualization feature.
It reads the SWE-bench Verified dataset once, indexes every row by ``instance_id``,
enumerates the target instances from the telemetry subfolders, and writes one
``metadata.json`` per task under ``base-commits/<instance_id>/``.

The emitted ``metadata.json`` is the stable per-task record of where the agent
started and what the task was, carrying ``instance_id``, ``repo``, ``base_commit``,
``environment_setup_commit``, and ``problem_statement``.

Design notes
------------
- The dataset is loaded once into an in-memory dict keyed by ``instance_id`` so a
  single pass over the ~2,294 rows serves every instance (Req 1.1, 1.3).
- An instance present in the telemetry dir but absent from the dataset split is
  reported and skipped by returning ``None`` -- never fatal (Req 1.5).
- No Docker is used anywhere (Req 1.6).
- UTF-8 ``pathlib`` I/O throughout.

Runnable in isolation for debugging:

    python -m viz.extract --telemetry trace-simulations/logs/telemetry/gpt5_6_luna \
        --out trace-simulations/base-commits
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Dataset coordinates (Req 1.1, 6.3). Loaded via the `datasets` library.
DATASET = "SWE-bench/SWE-bench_Verified"
SPLIT = "test"


def enumerate_instances(telemetry_dir: Path) -> list[str]:
    """Return the sorted Instance_Ids named by the subfolders under Telemetry_Dir.

    Only directory entries count as instances; files such as ``manifest.json`` that
    sit alongside the per-instance subfolders are ignored (Req 1.2).
    """
    telemetry_dir = Path(telemetry_dir)
    return sorted(p.name for p in telemetry_dir.iterdir() if p.is_dir())


def load_dataset_index() -> dict[str, dict]:
    """Load the Dataset once and index it by ``instance_id`` (Req 1.1, 1.3).

    Returns a mapping of ``instance_id`` -> dataset row. The import of ``datasets``
    is deferred to call time so the rest of the module (enumeration, metadata I/O)
    is usable without triggering a dataset download.
    """
    from datasets import load_dataset

    rows = load_dataset(DATASET, split=SPLIT)
    return {row["instance_id"]: row for row in rows}


def extract_one(instance_id: str, index: dict[str, dict], out_root: Path) -> dict | None:
    """Write ``base-commits/<instance_id>/metadata.json`` for one instance.

    Extracts the Metadata fields keyed by ``instance_id`` (Req 1.3) and writes them
    into the per-instance subfolder (Req 1.4). Returns the metadata dict, or ``None``
    when the instance is absent from the dataset split -- in which case the missing
    id is reported and processing of other instances can continue (Req 1.5).
    """
    row = index.get(instance_id)
    if row is None:
        print(f"  [skip] {instance_id} not found in {DATASET} split {SPLIT}")
        return None

    meta = {
        "instance_id": instance_id,
        "repo": row["repo"],  # e.g. "django/django"
        "base_commit": row["base_commit"],
        "environment_setup_commit": row.get("environment_setup_commit"),
        "problem_statement": row.get("problem_statement", ""),
    }

    inst_dir = Path(out_root) / instance_id
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return meta


def extract_all(telemetry_dir: Path, out_root: Path) -> dict[str, dict]:
    """Extract metadata for every instance enumerated from the telemetry dir.

    Loads the dataset index once, then writes one ``metadata.json`` per instance.
    Missing instances are reported and skipped. Returns ``instance_id`` -> metadata
    for the instances that were found and written.
    """
    instances = enumerate_instances(telemetry_dir)
    index = load_dataset_index()
    written: dict[str, dict] = {}
    for instance_id in instances:
        meta = extract_one(instance_id, index, out_root)
        if meta is not None:
            written[instance_id] = meta
    return written


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--telemetry",
        type=Path,
        default=Path("trace-simulations/logs/telemetry/gpt5_6_luna"),
        help="Telemetry_Dir whose subfolders enumerate the target Instance_Ids.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("trace-simulations/base-commits"),
        help="Base_Commits_Dir to write <instance_id>/metadata.json into.",
    )
    args = ap.parse_args()

    written = extract_all(args.telemetry, args.out)
    print(f"Wrote metadata for {len(written)} instance(s) under {args.out}")


if __name__ == "__main__":
    main()
