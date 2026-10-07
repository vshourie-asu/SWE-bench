# Implementation Plan: SWE-bench Multi-Level Change Graph

## Overview

Build a progressive multi-level change-graph visualization for one prototype instance (`scikit-learn__scikit-learn-13496`), reusing the existing `trace-simulations/viz` pipeline (`extract`, `clone`, `diffparse`, `summarize`). Three new Python modules (`structure.py`, `graphbuild.py`, `levelsummary.py`), a new orchestrator (`build_graph.py`), and a new self-contained SVG graph viewer (`graph.html`, replacing `index.html`) produce and render a `Graph_Document` with per-node change annotations and per-level LLM summaries along changed paths only.

All code is Python stdlib + `datasets` (dataset load is reused via `extract`), UTF-8 `pathlib` I/O, Windows/PowerShell + `trace-sims` conda env, no Docker, no secrets committed. Tests use the standard library `unittest`.

## Tasks

- [x] 0. Remove the orphaned old-pipeline functionality
  - Delete `trace-simulations/viz/index.html` (replaced by `graph.html`), `trace-simulations/viz/treebuild.py` (replaced by `graphbuild.py`), and `trace-simulations/build_viz.py` (replaced by `build_graph.py`). Confirm first that nothing retained imports `treebuild` or `build_viz`.
  - Delete the old flat outputs for all four instances: `trace-simulations/base-commits/<id>/viz_data.json` and `trace-simulations/viz/data/<id>.viz_data.json`. KEEP each `trace-simulations/base-commits/<id>/metadata.json`.
  - Remove any now-stale references to `index.html`/`build_viz.py`/`treebuild.py` in `trace-simulations/README.md` (the change-viz section is rewritten in task 8).
  - _Requirements: 3.5, 7.2_

- [x] 1. Implement the Structure_Extractor (`viz/structure.py`)
  - Implement a `Symbol` dataclass (`kind`, `name`, `start`, `end`, `children`) and `extract_symbols(source, module_name) -> Symbol`.
  - Use `ast` to extract module-level imports (`import`), module-level constants/assignments (`const`), top-level classes (`class`) with their methods as nested `function` nodes, and top-level functions (`function`). Record 1-based `start`/`end` line ranges using `lineno`/`end_lineno` (include decorator lines).
  - On `SyntaxError`/parse failure, return a childless module `Symbol` (Req 1.5).
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 7.4_

  - [ ]* 1.1 Write property test for symbol extraction ranges
    - **Property 1: Symbol extraction records valid, nested line ranges**
    - **Validates: Requirements 1.2, 1.3, 1.4**
    - On small snippets, assert imports/consts/classes/methods/functions are extracted with `1 <= start <= end` and child ranges nested within parent ranges; assert a `SyntaxError` snippet yields a childless module node.

- [ ] 2. Implement the Graph_Builder (`viz/graphbuild.py`)
  - [x] 2.1 Build the directory/module/symbol scaffold
    - Walk the working tree into `dir` nodes and per-file `module` nodes (prune `.git`/`__pycache__` etc., forward-slash paths, deterministic ordering). For Python files attach `structure.extract_symbols` children; non-Python files get a childless module node (Req 1.6). Assign stable `id` strings (`path::kind:name` chain).
    - _Requirements: 1.6, 3.1, 3.2, 7.4_

  - [x] 2.2 Map changes onto symbols and propagate
    - For each layer's `FileDiff` (from `diffparse`), map each changed line to the innermost symbol whose `[start,end]` contains it using the **old-side** line number; additions use the hunk's old-side anchor; lines outside any symbol attach to the module node; modules without symbols take all file changes (Req 2.1, 2.2, 2.5). Record `annotations[layer]={added,removed,hunks}` on direct leaf nodes keeping layers separate (Req 2.3, 2.6). Store per-layer `hunks` and a `source` slice only on direct class/function nodes (Req 3.3, 6.3).
    - Post-order pass sets `change_state` ∈ {none, ancestor, direct}, `changed`, and aggregates per-layer counts upward to the root (Req 2.4, 3.4).
    - Provide `build_graph(repo_dir, model, gold, test) -> dict` returning the root node.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.2, 3.3, 3.4, 6.3_

  - [ ]* 2.3 Write property tests for change mapping and propagation
    - **Property 3: Every changed line maps to exactly one innermost node** (Validates: Requirements 2.1, 2.2, 2.5)
    - **Property 4: Change propagation reaches the root** (Validates: Requirements 2.4, 3.4)
    - **Property 5: Layers remain distinct** (Validates: Requirements 2.6, 3.2)
    - Using the real `iforest.py` base-commit file + the model hunks from `preds.json`, assert the change maps to `IsolationForest.__init__` (and the docstring hunk to the class), that all ancestors become `ancestor`, and that a multi-layer node keeps separate per-layer entries.

  - [ ]* 2.4 Write property test for file-node preservation
    - **Property 2: Unparseable or non-Python files never lose their file node**
    - **Validates: Requirements 1.5, 1.6**
    - Assert a non-Python file and a syntactically invalid Python file each yield exactly one childless module/file node.

- [ ] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass; ask the user if questions arise.

- [x] 4. Implement the Level_Summarizer (`viz/levelsummary.py`)
  - Implement `summarize_graph(root, env_path, model)` doing a post-order walk that summarizes a node iff `change_state != "none"` (Req 4.1, 4.4). Direct function/class/module nodes summarized from their combined diff hunks via `summarize.summarize_file`; module/dir/class-with-changed-children nodes summarized from a rollup prompt built from changed children's names + summaries + aggregated counts (Req 4.2, 4.3). Reuse `summarize.load_credentials` and the litellm `openai/` double-prefix model (default `summarize.DEFAULT_MODEL`). On any failure use a templated fallback and set top-level `generated_with_llm=false`; never write the token (Req 4.5, 4.6, 4.7).
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 7.5_

  - [ ]* 4.1 Write property test for changed-path-only summaries
    - **Property 6: Summaries exist exactly on changed paths** (Validates: Requirements 4.1, 4.4)
    - **Property 7: Credentials never leak** (Validates: Requirements 4.7, 7.5)
    - With credentials forced unavailable, assert every `change_state != "none"` node gets a non-empty fallback summary, no `change_state == "none"` node gets a summary, and a fixture token never appears in the serialized document.

- [x] 5. Implement the orchestrator (`build_graph.py`)
  - Add `TARGET_INSTANCE = "scikit-learn__scikit-learn-13496"`, an argparse CLI (`--instance` default TARGET_INSTANCE, `--preds`, `--out`, `--viz-root`, `--cache`, `--env`, `--summary-model`, `--no-summaries`) with `__main__`. Make the `viz` package importable (sys.path insert like `build_viz.py`).
  - Run: `extract.extract_one` (repo/base_commit/patch/test_patch) -> `clone.ensure_clone`+`checkout` -> `diffparse.parse_patch` on model (from preds.json)/gold `patch`/`test_patch` -> `graphbuild.build_graph` -> `levelsummary.summarize_graph` (unless `--no-summaries`). Assemble the Graph_Document ({instance_id, repo, base_commit, generated_with_llm, layers, root}); write `base-commits/<id>/graph_data.json`, copy to `viz/data/<id>.graph.json`, and write `viz/manifest.json` with `viewer: "graph.html"` and the single instance entry.
  - _Requirements: 3.1, 3.5, 7.1, 7.2, 7.3_

  - [ ]* 5.1 Smoke build (no summaries)
    - Run `python trace-simulations/build_graph.py --no-summaries` and assert the Graph_Document + manifest are written and the changed path to `IsolationForest.__init__` exists with `change_state == "direct"`.
    - _Requirements: 3.1, 3.5_

- [x] 6. Build the Graph_Viewer (`viz/graph.html`, replaces `index.html`)
  - Single self-contained HTML with inline CSS + vanilla JS, SVG rendering, no external/CDN deps (Req 5.1).
  - Load `manifest.json` then `data/<id>.graph.json` via `fetch`. Compute the initial expansion set = the changed paths (root -> each `direct` node); render unchanged subtrees collapsed with a child-count badge (Req 5.2). Activating a node expands/collapses one level (Req 5.3).
  - Node-link SVG layout (hand-rolled tidy-tree: depth = x, incremental y; cubic-bezier containment links) over only visible nodes (Req 5.5). Highlight `direct` vs `ancestor` vs `none` distinctly, with per-layer model/gold/test accents, a legend, layer toggles, and non-color-alone markers (kind label, `+/-` badge) (Req 5.4).
  - Detail pane on selection: summary (Req 6.2); for `direct` nodes, per-layer diff hunks add/remove/context styled (Req 6.1); for class/function nodes, the stored `source` lines as context beside the diff (Req 6.3); for `ancestor` nodes, summary + aggregated descendant counts (Req 6.4).
  - Document the `file://` fetch limitation + `python -m http.server` inline. Remove/replace the old `index.html` so the manifest's `viewer` points at `graph.html` (Req 5.6).
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4_

- [x] 7. Real end-to-end build with LLM summaries and verification
  - Run `python trace-simulations/build_graph.py` (LLM summaries ON) for the Target_Instance using the repo-root `.env`; confirm `generated_with_llm == true`, summaries present on all changed-path nodes (dir/module/class/function), none on unchanged nodes, and no token in any artifact. Serve `trace-simulations/viz/` via `python -m http.server` and confirm `graph.html`, `manifest.json`, and the graph data load over HTTP (not `file://`). If the endpoint is unavailable, report it and fall back to `--no-summaries` outputs.
  - _Requirements: 4.5, 4.6, 5.6, 7.1_

- [x] 8. Update `trace-simulations/README.md`
  - Document `build_graph.py` (flags, the single-instance scope, LLM-on default + `--no-summaries`), the new `graph.html` viewer and how it replaces the tree view, the multi-level graph/summaries model, and serving via `python -m http.server`. Reinforce secret hygiene.
  - _Requirements: 7.1, 7.3, 7.5_

- [ ] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass; ask the user if questions arise.

## Notes

- Tasks marked `*` are optional test tasks; core implementation tasks are never optional.
- Property tests map to the design's Correctness Properties 1-7.
- Reuses `extract.py`, `clone.py`, `diffparse.py`, `summarize.py` unchanged; adds `structure.py`, `graphbuild.py`, `levelsummary.py`, `build_graph.py`, `graph.html`.
- Line-number basis: changes are mapped using the diff's **old-side** line numbers because the `ast` tree is parsed from the base-commit (pre-change) source.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["0", "1"] },
    { "id": 1, "tasks": ["1.1", "2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "4"] },
    { "id": 4, "tasks": ["4.1", "5"] },
    { "id": 5, "tasks": ["5.1", "6"] },
    { "id": 6, "tasks": ["7", "8"] }
  ]
}
```
