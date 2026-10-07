# Requirements Document

## Introduction

This feature adds tooling under `trace-simulations/` to support an HCI paper visualization of the code changes made by the agent `gpt5_6_luna` on four SWE-bench Verified tasks. The tooling extracts each task's base commit and metadata from the SWE-bench Verified dataset, clones each repository locally at that base commit (no Docker), builds a single JSON data file describing the full repository file tree with per-file and per-line change annotations for the model patch, gold patch, and test patch, generates per-file natural-language summaries of the changes via an LLM endpoint with a graceful fallback, and scaffolds a self-contained static HTML viewer that renders the full tree with changed files highlighted, per-line diff hunks, and the summaries.

The scope is fixed to four instances: `astropy__astropy-14369`, `django__django-14238`, `pydata__xarray-6992`, and `scikit-learn__scikit-learn-13496`. All scripts run on Windows/PowerShell in the `trace-sims` conda environment and must not commit secrets.

## Glossary

- **Change_Viz_Tooling**: The complete set of Python scripts and the static HTML viewer added under `trace-simulations/` for this feature.
- **Base_Commit_Extractor**: The component that reads the SWE-bench Verified dataset and writes per-task base commit and metadata files.
- **Repo_Cloner**: The component that creates and caches local git clones of task repositories checked out at the base commit.
- **Viz_Data_Builder**: The component that walks a checked-out working tree and builds the `viz_data.json` document, including file tree and change annotations.
- **Summary_Generator**: The component that produces per-file natural-language summaries of changes using the LLM endpoint, with a fallback.
- **Viewer**: The single self-contained static `index.html` file that renders the visualization by loading `viz_data.json`.
- **Dataset**: SWE-bench Verified, loaded via the `datasets` library from `SWE-bench/SWE-bench_Verified`, split `test`.
- **Instance_Id**: The unique task identifier used by SWE-bench (e.g., `django__django-14238`).
- **Base_Commit**: The `base_commit` field for a task in the Dataset, the commit the agent started from.
- **Model_Patch**: The agent-produced patch for a task, read from the `model_patch` field in `preds.json`.
- **Gold_Patch**: The reference fix for a task, read from the `patch` field in the Dataset.
- **Test_Patch**: The test changes for a task, read from the `test_patch` field in the Dataset.
- **Metadata**: The per-task fields `repo`, `base_commit`, `environment_setup_commit`, and `problem_statement` from the Dataset.
- **Telemetry_Dir**: The directory `trace-simulations/logs/telemetry/gpt5_6_luna`, whose per-instance subfolders enumerate the four target tasks.
- **Preds_File**: The predictions file at `trace-simulations/logs/preds/gpt5_6_luna/preds.json`, mapping Instance_Id to `model_patch`.
- **Base_Commits_Dir**: The output directory `trace-simulations/base-commits/`, containing one subfolder per Instance_Id.
- **Hunk**: A contiguous block of per-line additions and removals within a single file in a patch.
- **LLM_Endpoint**: The OpenAI-compatible CreateAI endpoint configured by `OPENAI_API_BASE` and `OPENAI_API_KEY` in the repo-root `.env` file, called using the litellm `openai/` double-prefix convention described in `trace-simulations/README.md`.
- **Target_Tasks**: The four Instance_Ids `astropy__astropy-14369`, `django__django-14238`, `pydata__xarray-6992`, and `scikit-learn__scikit-learn-13496`.

## Requirements

### Requirement 1: Base commit and metadata extraction

**User Story:** As an HCI researcher, I want each target task's base commit and metadata extracted from the Dataset, so that I have a stable per-task record of where the agent started and what the task was.

#### Acceptance Criteria

1. WHEN the Base_Commit_Extractor runs, THE Base_Commit_Extractor SHALL read the Dataset `SWE-bench/SWE-bench_Verified` split `test` using the `datasets` library.
2. THE Base_Commit_Extractor SHALL process each Instance_Id enumerated by the subfolders of the Telemetry_Dir.
3. WHEN the Base_Commit_Extractor processes an Instance_Id, THE Base_Commit_Extractor SHALL extract the Metadata fields `repo`, `base_commit`, `environment_setup_commit`, and `problem_statement` keyed by Instance_Id.
4. WHEN the Base_Commit_Extractor extracts Metadata for an Instance_Id, THE Base_Commit_Extractor SHALL create the subfolder `trace-simulations/base-commits/<instance_id>/` and write the Base_Commit and Metadata into that subfolder.
5. IF an Instance_Id from the Telemetry_Dir is absent from the Dataset split `test`, THEN THE Base_Commit_Extractor SHALL report the missing Instance_Id and continue processing the remaining Instance_Ids.
6. THE Base_Commit_Extractor SHALL perform extraction without invoking Docker.

### Requirement 2: Local repository clone and checkout

**User Story:** As an HCI researcher, I want each task's repository cloned locally at its base commit with clones reused across tasks, so that I can read real file contents without rebuilding environments.

#### Acceptance Criteria

1. WHEN the Repo_Cloner processes an Instance_Id, THE Repo_Cloner SHALL create a local git clone of the task `repo` using the local git executable.
2. WHEN the Repo_Cloner has a local clone for a `repo`, THE Repo_Cloner SHALL check out the Base_Commit for the Instance_Id in the working tree.
3. WHERE a local clone for a `repo` already exists from a previously processed Instance_Id, THE Repo_Cloner SHALL reuse the existing clone instead of creating a new clone.
4. THE Repo_Cloner SHALL perform all repository operations without invoking Docker.
5. IF a clone or checkout operation fails for an Instance_Id, THEN THE Repo_Cloner SHALL report the failure for that Instance_Id and continue processing the remaining Instance_Ids.

### Requirement 3: Full file tree with change annotations

**User Story:** As an HCI researcher, I want a single JSON data file describing the full repository tree with change annotations, so that the viewer can show every file with changes highlighted in context.

#### Acceptance Criteria

1. WHEN the Viz_Data_Builder processes an Instance_Id, THE Viz_Data_Builder SHALL walk the checked-out working tree and record every file and every directory in the file tree.
2. WHEN the Viz_Data_Builder records a file in the file tree, THE Viz_Data_Builder SHALL include the real file contents from the working tree.
3. WHEN the Viz_Data_Builder processes an Instance_Id, THE Viz_Data_Builder SHALL read the Model_Patch for the Instance_Id from the Preds_File, the Gold_Patch from the Dataset, and the Test_Patch from the Dataset.
4. WHEN the Viz_Data_Builder processes the Model_Patch, Gold_Patch, and Test_Patch, THE Viz_Data_Builder SHALL produce per-file change annotations that identify, for each patch type, which files are changed.
5. WHEN the Viz_Data_Builder produces change annotations for a changed file, THE Viz_Data_Builder SHALL record the per-line Hunks and the added-line count and removed-line count for that file.
6. WHEN the Viz_Data_Builder finishes processing an Instance_Id, THE Viz_Data_Builder SHALL write a `viz_data.json` document containing the full file tree and the change annotations for that Instance_Id.

### Requirement 4: Per-file natural-language summaries

**User Story:** As an HCI researcher, I want a plain-language summary of what changed in each modified file, so that raters can understand the changes without reading every diff line.

#### Acceptance Criteria

1. WHEN the Summary_Generator processes a changed file for an Instance_Id, THE Summary_Generator SHALL request a natural-language summary of that file's changes from the LLM_Endpoint using the credentials `OPENAI_API_BASE` and `OPENAI_API_KEY` read from the repo-root `.env` file.
2. WHEN the Summary_Generator calls the LLM_Endpoint, THE Summary_Generator SHALL use the litellm `openai/` double-prefix model-name convention described in `trace-simulations/README.md`.
3. WHEN the Summary_Generator receives a summary for a changed file, THE Summary_Generator SHALL store the summary in the `viz_data.json` document associated with that file.
4. IF the LLM_Endpoint is unreachable or the credentials are unavailable, THEN THE Summary_Generator SHALL record a fallback summary for the affected files and continue processing the remaining files.
5. THE Summary_Generator SHALL read credentials from the repo-root `.env` file without writing the credential values into any committed file.

### Requirement 5: Self-contained static viewer

**User Story:** As an HCI researcher, I want a self-contained static HTML viewer, so that I can open the visualization by serving a single folder without any build step or external dependencies.

#### Acceptance Criteria

1. THE Viewer SHALL be a single `index.html` file containing inline vanilla JavaScript and inline CSS.
2. THE Viewer SHALL load its data by fetching `viz_data.json` at runtime.
3. THE Viewer SHALL render without referencing any build tooling, external package dependency, or content delivery network resource.
4. WHEN the Viewer loads `viz_data.json`, THE Viewer SHALL render the full repository file tree with changed files visually highlighted in the context of the tree.
5. WHEN a user selects a changed file in the Viewer, THE Viewer SHALL display the per-line diff Hunks for that file.
6. WHEN the Viewer displays a changed file, THE Viewer SHALL display the natural-language summary stored for that file.
7. THE Viewer SHALL function when served from its containing folder via `python -m http.server`.

### Requirement 6: Environment, scope, and secret hygiene

**User Story:** As an HCI researcher, I want all tooling to run in the documented Windows environment for the four fixed tasks, so that the workflow is reproducible without secret leakage.

#### Acceptance Criteria

1. THE Change_Viz_Tooling SHALL place all Python scripts under the `trace-simulations/` directory.
2. THE Change_Viz_Tooling SHALL run on Windows using PowerShell in the `trace-sims` conda environment.
3. THE Change_Viz_Tooling SHALL load the Dataset using the `datasets` library.
4. THE Change_Viz_Tooling SHALL process the Target_Tasks `astropy__astropy-14369`, `django__django-14238`, `pydata__xarray-6992`, and `scikit-learn__scikit-learn-13496`.
5. THE Change_Viz_Tooling SHALL read the LLM_Endpoint token from the repo-root `.env` file and SHALL exclude the token value from every file written to the repository.
