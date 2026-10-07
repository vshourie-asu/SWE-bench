#!/usr/bin/env python3
"""Orchestrator: build the multi-level Graph_Document for the Target_Instance.

Implements Requirement 7 (scope / environment / reuse) and Requirements 3.1 and
3.5 of the SWE-bench change-graph feature. This entrypoint drives the full
pipeline for a single prototype instance (``scikit-learn__scikit-learn-13496``),
reusing the existing ``viz`` package components end to end:

  1. :func:`viz.extract.load_dataset_index` + :func:`viz.extract.extract_one`
     resolve the instance's ``repo`` / ``base_commit`` (and write the standalone
     ``metadata.json``); the dataset row is also read directly for the gold
     ``patch`` and ``test_patch``.
  2. :func:`viz.clone.ensure_clone` + :func:`viz.clone.checkout` put the repo's
     working tree at the base commit (reused from the shared ``.repo-cache``).
  3. :func:`viz.diffparse.parse_patch` parses the model patch (from
     ``preds.json``), the gold ``patch``, and the ``test_patch`` into three
     ``list[FileDiff]`` layers.
  4. :func:`viz.graphbuild.build_graph` assembles the directory -> module ->
     symbol hierarchy and overlays the three change layers.
  5. :func:`viz.levelsummary.summarize_graph` attaches per-level LLM summaries
     along changed paths (skipped under ``--no-summaries``; an offline fallback
     fills changed-path nodes with templated summaries instead).
  6. The assembled Graph_Document is written to the canonical
     ``base-commits/<id>/graph_data.json``, copied to
     ``viz/data/<id>.graph.json``, and the viewer ``manifest.json`` is updated to
     point at ``graph.html`` and the single instance entry.

The ``viz`` package is made importable from the repository root exactly like the
old ``build_viz.py`` did -- by inserting this file's own directory onto
``sys.path`` -- so ``from viz import ...`` works regardless of the caller's CWD.

Scope and environment (Req 7.1, 7.3)
------------------------------------
Defaults target the single ``Target_Instance``; ``--instance`` is accepted so the
same entrypoint can build the others later. Standard library + ``datasets`` only,
UTF-8 ``pathlib`` I/O, Windows/PowerShell + ``trace-sims`` conda env, no Docker.

Secret hygiene (Req 7.5)
------------------------
Credentials are only ever read inside :mod:`viz.summarize` from the repo-root
``.env`` and used to build request headers; no credential value is written into
the Graph_Document, the manifest, or any other emitted artifact.

Usage::

    python trace-simulations/build_graph.py                 # LLM summaries on
    python trace-simulations/build_graph.py --no-summaries  # offline fallback
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the ``viz`` package importable from the repo root (mirrors the old
# build_viz.py): put this file's own directory (trace-simulations/) on sys.path
# so ``from viz import ...`` resolves no matter where the script is launched.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from viz import extract, clone, diffparse, graphbuild, levelsummary, summarize

# The single prototype instance this iteration targets (Req 7.1).
TARGET_INSTANCE = "scikit-learn__scikit-learn-13496"

# Fixed, deterministic layer order shared with the Graph_Builder.
_LAYERS = ["model", "gold", "test"]


def _load_model_patch(preds_path: Path, instance_id: str) -> str:
    """Return the ``model_patch`` for ``instance_id`` from ``preds.json``.

    ``preds.json`` is a top-level object keyed by ``instance_id`` whose values
    carry a ``model_patch`` string. A missing instance or missing patch yields an
    empty string so the model layer simply contributes no changes rather than
    aborting the build.
    """
    try:
        text = preds_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"  [warn] could not read preds {preds_path}: {type(exc).__name__}")
        return ""
    preds = json.loads(text)
    entry = preds.get(instance_id)
    if not entry:
        print(f"  [warn] {instance_id} not present in {preds_path}")
        return ""
    return entry.get("model_patch", "") or ""


def _apply_fallback_summaries(root: dict) -> None:
    """Fill templated summaries on every changed-path node (offline mode).

    Mirrors the Level_Summarizer's changed-path scope (``change_state != "none"``)
    without any endpoint call: each changed node gets a deterministic templated
    sentence from its aggregated per-layer counts via
    :func:`viz.summarize.fallback_summary`, using the node's path/name as the
    label. The credential path is never touched (Req 4.6-style offline fallback).
    """

    def visit(node: dict) -> None:
        for child in node.get("children") or []:
            visit(child)
        if node.get("change_state", "none") == "none":
            return
        added = 0
        removed = 0
        for layer_ann in (node.get("annotations") or {}).values():
            if not layer_ann:
                continue
            added += int(layer_ann.get("added", 0) or 0)
            removed += int(layer_ann.get("removed", 0) or 0)
        label = node.get("path") or node.get("name") or node.get("id") or node.get("kind", "node")
        node["summary"] = summarize.fallback_summary(label, added, removed)

    visit(root)


def _write_json(path: Path, payload: dict) -> None:
    """Write ``payload`` as pretty UTF-8 JSON, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _update_manifest(manifest_path: Path, entry: dict) -> None:
    """Merge ``entry`` into the viewer manifest, keeping all built instances.

    Reads the existing ``manifest.json`` (if any), replaces or appends the entry
    for ``entry['instance_id']``, and writes the list back sorted by instance id.
    This lets successive single-instance builds accumulate into one manifest so
    the viewer dropdown lists every instance built so far, while re-running an
    instance updates its entry idempotently. A missing or malformed manifest is
    treated as empty.
    """
    instances: list[dict] = []
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            instances = [
                e
                for e in existing.get("instances", [])
                if isinstance(e, dict) and e.get("instance_id") != entry["instance_id"]
            ]
        except (OSError, ValueError):
            instances = []
    instances.append(entry)
    instances.sort(key=lambda e: e.get("instance_id", ""))
    _write_json(manifest_path, {"viewer": "graph.html", "instances": instances})


def build_instance(
    instance_id: str,
    *,
    preds_path: Path,
    out_root: Path,
    viz_root: Path,
    cache_root: Path,
    env_path: Path,
    summary_model: str,
    no_summaries: bool,
) -> bool:
    """Build and write the Graph_Document for a single instance.

    Returns ``True`` on success, ``False`` when the instance could not be built
    (reported, no document written). Any unexpected error is caught and reported
    so one bad instance never crashes the entrypoint (Req 7.1).
    """
    print(f"[build_graph] instance: {instance_id}")
    try:
        # 1. Dataset metadata + the raw row for gold patch / test_patch.
        index = extract.load_dataset_index()
        meta = extract.extract_one(instance_id, index, out_root)
        if meta is None:
            print(f"  [error] {instance_id} not found in dataset; nothing written")
            return False
        row = index[instance_id]
        repo = meta["repo"]
        base_commit = meta["base_commit"]
        gold_patch = row.get("patch", "") or ""
        test_patch = row.get("test_patch", "") or ""
        print(f"  repo={repo} base_commit={base_commit}")

        # 2. Clone (reuse cache) + checkout the base commit.
        repo_dir = clone.ensure_clone(repo, cache_root)
        clone.checkout(repo_dir, base_commit)

        # 3. Parse the three change layers into list[FileDiff].
        model_patch = _load_model_patch(preds_path, instance_id)
        model = diffparse.parse_patch(model_patch)
        gold = diffparse.parse_patch(gold_patch)
        test = diffparse.parse_patch(test_patch)
        print(
            f"  layers: model={len(model)} file(s), "
            f"gold={len(gold)} file(s), test={len(test)} file(s)"
        )

        # 4. Build the multi-level graph.
        root = graphbuild.build_graph(repo_dir, model, gold, test)

        # 5. Summaries along changed paths (LLM on by default).
        if no_summaries:
            _apply_fallback_summaries(root)
            used_llm = False
            print("  summaries: offline fallback (--no-summaries)")
        else:
            used_llm = levelsummary.summarize_graph(root, env_path, summary_model)
            print(f"  summaries: generated_with_llm={used_llm}")

        # 6. Assemble + write the Graph_Document and update the viewer manifest.
        document = {
            "instance_id": instance_id,
            "repo": repo,
            "base_commit": base_commit,
            "generated_with_llm": used_llm,
            "layers": list(_LAYERS),
            "root": root,
        }

        canonical = out_root / instance_id / "graph_data.json"
        _write_json(canonical, document)

        viz_data = viz_root / "data" / f"{instance_id}.graph.json"
        _write_json(viz_data, document)

        entry = {
            "instance_id": instance_id,
            "repo": repo,
            "data": f"data/{instance_id}.graph.json",
        }
        _update_manifest(viz_root / "manifest.json", entry)

        print(f"  wrote {canonical}")
        print(f"  wrote {viz_data}")
        print(f"  wrote {viz_root / 'manifest.json'}")
        return True
    except Exception as exc:  # one bad instance must not crash the entrypoint
        print(f"  [error] failed to build {instance_id}: {type(exc).__name__}: {exc}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--instance",
        default=TARGET_INSTANCE,
        help="Instance_Id to build (default: the single Target_Instance).",
    )
    ap.add_argument(
        "--preds",
        type=Path,
        default=Path("trace-simulations/logs/preds/gpt5_6_luna/preds.json"),
        help="preds.json containing the model_patch per instance.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("trace-simulations/base-commits"),
        help="Base_Commits_Dir for <id>/graph_data.json (canonical copy).",
    )
    ap.add_argument(
        "--viz-root",
        type=Path,
        default=Path("trace-simulations/viz"),
        help="Viewer root for data/<id>.graph.json and manifest.json.",
    )
    ap.add_argument(
        "--cache",
        type=Path,
        default=Path("trace-simulations/.repo-cache"),
        help="Repo clone cache (shared across instances).",
    )
    ap.add_argument(
        "--env",
        type=Path,
        default=Path(".env"),
        help="Repo-root .env with OPENAI_API_BASE / OPENAI_API_KEY.",
    )
    ap.add_argument(
        "--summary-model",
        default=summarize.DEFAULT_MODEL,
        help="Gateway model name for the summary endpoint (single openai/ prefix, "
        "e.g. openai/gpt5_6_luna).",
    )
    ap.add_argument(
        "--no-summaries",
        action="store_true",
        help="Skip the LLM endpoint; fill changed-path nodes with templated summaries.",
    )
    args = ap.parse_args()

    ok = build_instance(
        args.instance,
        preds_path=args.preds,
        out_root=args.out,
        viz_root=args.viz_root,
        cache_root=args.cache,
        env_path=args.env,
        summary_model=args.summary_model,
        no_summaries=args.no_summaries,
    )
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
