# Implementation Plan: SWE-bench Change Visualization

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement each step with incremental progress. Make sure that each prompt builds on the previous prompts, and ends with wiring things together. There should be no hanging or orphaned code that isn't integrated into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

The pipeline is a stdlib-plus-`datasets` Python package under `trace-simulations/`: a `build_viz.py` orchestrator driving four components (`extract`, `clone`, `diffparse`, `treebuild`, `summarize`) in the `viz/` package, writing `base-commits/<id>/` outputs and a self-contained `viz/index.html` viewer. All code runs on Windows/PowerShell in the `trace-sims` conda environment, uses UTF-8 `pathlib` I/O, and never commits secrets. Tests use the standard library `unittest`.

## Tasks

- [x] 1. Scaffold the `viz/` package and ignore generated artifacts
  - Create `trace-simulations/viz/__init__.py` with a module-level docstring describing the package.
  - Add stdlib imports skeleton and shared constants file usage conventions (UTF-8, forward-slash paths).
  - Add `trace-simulations/.repo-cache/` and the large generated viz copies (`trace-simulations/viz/data/`) to the repo-root `.gitignore` so clones and bulky JSON are never committed.
  - _Requirements: 6.1, 6.2_

- [ ] 2. Implement the unified-diff parser (`viz/diffparse.py`)
  - [x] 2.1 Implement `diffparse.py` data model and parser
    - Define `LineChange`, `FileDiff`, `Hunk` dataclasses matching the design schema.
    - Implement `parse_patch(patch_text)` that prefers `unidiff` when importable and falls back to a stdlib line-oriented state machine parsing `diff --git`, `---`/`+++`, and `@@` headers.
    - Normalize paths (strip `a/`/`b/` prefixes, forward slashes), handle `/dev/null` create/delete, tally `added`/`removed` from classified lines, and maintain running `old_lineno`/`new_lineno`.
    - _Requirements: 3.3, 3.4, 3.5_

  - [ ]* 2.2 Write property test for diff path/count fidelity
    - **Property 6: Diff annotations faithfully reflect the patches**
    - **Validates: Requirements 3.4, 3.5**
    - Assert against the four known `model_patch` diffs (django 1 add/1 remove, astropy 2 add/1 remove, xarray 2 add/1 remove, scikit-learn 6 add/2 remove across three hunks): assert parsed paths, hunk counts, and per-file add/remove totals; every hunk line classified as exactly one of add/remove/context.

  - [ ]* 2.3 Write round-trip reconstruction test for `diffparse`
    - Reconstruct the `+`/`-` line sets from parsed hunks and compare to the raw diff bodies for the four known diffs.
    - _Requirements: 3.5_

- [ ] 3. Implement the base-commit extractor (`viz/extract.py`)
  - [x] 3.1 Implement `extract.py`
    - Add `DATASET`/`SPLIT` constants, `enumerate_instances(telemetry_dir)` (sorted subfolder names, ignoring non-directories), `load_dataset_index()` (load once, index by `instance_id`), and `extract_one(instance_id, index, out_root)`.
    - Write `base-commits/<instance_id>/metadata.json` with `instance_id`, `repo`, `base_commit`, `environment_setup_commit`, `problem_statement`; report and skip (return `None`) when the instance is absent from the dataset. No Docker.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 6.3_

  - [ ]* 3.2 Write property test for instance enumeration
    - **Property 1: Instance enumeration is complete and exact**
    - **Validates: Requirements 1.2**
    - On a synthetic telemetry dir with mixed files and subfolders, assert `enumerate_instances` returns exactly the subfolder-name set, no extras/missing.

  - [ ]* 3.3 Write property test for metadata round-trip
    - **Property 2: Metadata extraction round-trips the required fields**
    - **Validates: Requirements 1.3, 1.4**
    - Feed a synthetic dataset-row dict through `extract_one`, read `metadata.json` back, assert the five required fields equal the row values. Also assert a missing instance returns `None` without raising (Req 1.5).

- [ ] 4. Implement the repo cloner (`viz/clone.py`)
  - [x] 4.1 Implement `clone.py`
    - Implement `cache_path(repo, cache_root)` (`org/name` -> `<org>__<repo>`), `ensure_clone` (reuse when `.git` exists, else full clone via `_git`), `checkout` with `fetch origin <sha>` fallback on checkout miss, and `_git` capturing stdout/stderr with `check=True`.
    - Use `pathlib.Path` and list-form `subprocess.run` (no shell) for Windows path safety. No Docker.
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [ ]* 4.2 Write property test for cache-path determinism and injectivity
    - **Property 3: Clone cache paths are deterministic and collision-free**
    - **Validates: Requirements 2.3**
    - Assert equal repos map to equal paths and distinct repos map to distinct paths across a set of `org/name` strings.

- [ ] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Implement the viz data builder (`viz/treebuild.py`)
  - [x] 6.1 Implement tree walk and file-node reader
    - Add `TEXT_MAX_BYTES`, `SKIP_DIRS`, `BINARY_SNIFF_BYTES` constants; implement `build_tree(repo_dir)` (nested dir/file tree, prune `SKIP_DIRS`, forward-slash relative paths) and `read_file_node` (null-byte binary sniff, size cap, `content: null` + `truncated: true` for binary/oversized, else UTF-8 content).
    - _Requirements: 3.1, 3.2_

  - [x] 6.2 Implement annotation merge
    - Implement `annotate(tree, model, gold, test)` and `find_node` (create a placeholder file node when a patch path is absent from the tree), attaching per-layer `added`/`removed`/`hunks` under each file node's `annotations`.
    - _Requirements: 3.4, 3.5_

  - [ ]* 6.3 Write property test for tree completeness
    - **Property 4: The file tree records every non-ignored file and directory**
    - **Validates: Requirements 3.1**
    - On a tiny synthetic working tree, assert every on-disk file/dir (minus `SKIP_DIRS`) appears exactly once at its correct path.

  - [ ]* 6.4 Write property test for file content fidelity
    - **Property 5: File content fidelity**
    - **Validates: Requirements 3.2**
    - Assert a non-binary, under-cap file's recorded `content` equals its actual text; assert an oversized/binary file has `content: null` and `truncated: true`.

  - [ ]* 6.5 Write unit test for annotation placement
    - Assert annotations land on the correct node path, including a placeholder node created for a patch path absent from the tree, and that layers are stored independently.
    - _Requirements: 3.4_

- [ ] 7. Implement the summary generator (`viz/summarize.py`)
  - [x] 7.1 Implement `summarize.py`
    - Implement `load_credentials(env_path)` (parse `OPENAI_API_BASE`/`OPENAI_API_KEY` from repo-root `.env`, never logged), `summarize_file` (stdlib `urllib.request` POST to `{base}/chat/completions` with `Authorization: Bearer`, litellm `openai/` double-prefix model, small `max_tokens`), `fallback_summary`, and `summarize_all` (per-file try/except -> fallback, continue; set `generated_with_llm`).
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 6.5_

  - [ ]* 7.2 Write property test for fallback coverage
    - **Property 8: Fallback summaries cover every changed file**
    - **Validates: Requirements 4.4**
    - With credentials forced unavailable, assert every changed file receives a non-empty fallback summary and processing covers all files without aborting.

  - [ ]* 7.3 Write property test for secret hygiene
    - **Property 9: Credential values never leak into written artifacts**
    - **Validates: Requirements 4.5, 6.5**
    - Assert a token value read from a fixture `.env` never appears in the produced document or any emitted output string.

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Implement the orchestrator (`build_viz.py`)
  - [x] 9.1 Implement `build_viz.py` CLI and pipeline
    - Add `TARGET_TASKS`, an `argparse` CLI (`--telemetry`, `--preds`, `--out`, `--viz-root`, `--cache`, `--env`, `--summary-model`, `--no-summaries`) with `__main__` block.
    - Load dataset index and `preds.json` once; per instance run extract -> clone+checkout -> build tree + annotate -> summarize inside a `try/except` so one failure is reported and skipped (Req 2.5).
    - Write canonical `base-commits/<id>/viz_data.json`, copy to `viz/data/<id>.viz_data.json`, and append to `viz/manifest.json`.
    - _Requirements: 2.5, 3.6, 4.3, 6.1, 6.4_

  - [ ]* 9.2 Write property test for viz_data.json round-trip
    - **Property 7: viz_data.json serialization round-trips**
    - **Validates: Requirements 3.6**
    - Write a generated viz document to JSON and load it back; assert equality with the original.

- [x] 10. Build the self-contained viewer (`viz/index.html`)
  - Single `index.html` with inline vanilla JS and inline CSS, no external/CDN references and no build step.
  - On load `fetch('manifest.json')` to populate the instance dropdown; selecting an instance fetches `data/<id>.viz_data.json`.
  - Render the full tree as a collapsible nested list with changed files highlighted in context; color changes by layer (model/gold/test) with a legend and a layer toggle.
  - On selecting a changed file, show per-line hunks (add green, remove red, context neutral) reconstructed from `annotations[layer].hunks` plus the file's `summary`.
  - Document the `file://` fetch limitation inline and the `python -m http.server` serving command.
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

- [x] 11. End-to-end smoke run and verification
  - Run `build_viz.py` on the four `Target_Tasks` and assert four `viz_data.json` files plus a four-entry `viz/manifest.json` are produced; verify the viewer renders the tree, highlights, hunks, and summaries when served via `python -m http.server` from `trace-simulations/viz/`.
  - _Requirements: 6.4, 5.4, 5.5, 5.6, 5.7_

- [x] 12. Update `trace-simulations/README.md` with usage
  - Document the `trace-sims` conda env + `.env` credential setup, the `build_viz.py` command and flags, and the viewer serving instructions (`python -m http.server`), reinforcing that secrets are never committed.
  - _Requirements: 6.1, 6.2, 6.5_

- [ ] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific granular requirements for traceability.
- Property tests map directly to the design's Correctness Properties (Properties 1-9); the I/O-heavy dataset/git/HTTP paths are validated by the end-to-end smoke run.
- Checkpoints ensure incremental validation at natural breaks.
- All code is stdlib + `datasets`, UTF-8 `pathlib` I/O, no Docker, Windows/PowerShell + `trace-sims` conda env.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2.1", "3.1", "4.1"] },
    { "id": 1, "tasks": ["2.2", "2.3", "3.2", "3.3", "4.2", "6.1"] },
    { "id": 2, "tasks": ["6.2", "7.1"] },
    { "id": 3, "tasks": ["6.3", "6.4", "6.5", "7.2", "7.3", "9.1", "10"] },
    { "id": 4, "tasks": ["9.2", "11", "12"] }
  ]
}
```
