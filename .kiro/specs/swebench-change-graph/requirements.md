# Requirements Document

## Introduction

This feature evolves the existing SWE-bench change-visualization tooling under `trace-simulations/` from a plain collapsible file tree into a progressive, multi-level **change graph**. The current viewer forces a user to click blindly through thousands of files to find the few that changed. The new viewer shows the codebase as a node-link graph at increasing levels of detail — directory, module (with its imports and module-level constants), class, and function/method — where changes are highlighted at every level and the graph stays mostly collapsed, expanding on demand along changed paths only.

A new Python component extracts code structure from the checked-out base-commit sources using the standard-library `ast` module, maps the model/gold/test diff hunks onto the enclosing symbols, and generates real LLM summaries at every level along changed paths. The output replaces the old `viz_data.json` shape with a multi-level graph document, and the new graph viewer replaces the old file-tree `index.html`.

This is a prototype scoped to **one instance** first: `scikit-learn__scikit-learn-13496`. The remaining three instances are out of scope for this iteration. Symbol extraction is Python-only; non-Python files degrade to file-level nodes. All scripts run on Windows/PowerShell in the `trace-sims` conda environment and must not commit secrets.

## Glossary

- **Change_Graph_Tooling**: The complete set of Python scripts and the static graph viewer added/modified under `trace-simulations/` for this feature.
- **Structure_Extractor**: The component that parses Python source files with the `ast` module into a hierarchy of code symbols (module, imports, module-level constants, classes, functions/methods).
- **Graph_Builder**: The component that assembles the multi-level node hierarchy, overlays change annotations by mapping diff hunks onto symbols, propagates change flags to ancestors, and writes the Graph_Document.
- **Level_Summarizer**: The component that generates LLM summaries for nodes along changed paths at every level, rolling child summaries up into parent summaries.
- **Graph_Viewer**: The single self-contained static HTML viewer that renders the Graph_Document as an on-demand, collapsible node-link graph with change highlighting and a code/diff detail view.
- **Graph_Document**: The JSON artifact describing the multi-level node hierarchy with per-node change annotations and summaries, replacing the old `viz_data.json` shape for the target instance.
- **Node**: A single element in the graph hierarchy with a `kind` of one of `dir`, `module`, `import`, `const`, `class`, or `function`.
- **Level**: The depth tier of the hierarchy: directory, module, intra-module symbol (import / const / class), and function/method.
- **Symbol**: A code element extracted from a Python source file by the Structure_Extractor: a module-level import, a module-level constant/assignment, a class, or a function/method.
- **Layer**: One of the three change sources `model`, `gold`, or `test`, as parsed from the corresponding patch.
- **Changed_Node**: A Node that is directly changed by at least one Layer, or an ancestor of such a Node.
- **Changed_Path**: The chain of Nodes from the root to a directly changed Node, inclusive.
- **Target_Instance**: The single SWE-bench Verified instance built in this iteration: `scikit-learn__scikit-learn-13496`.
- **Base_Commit_Sources**: The working-tree files of the Target_Instance's repository checked out at its base commit, produced by the existing Repo_Cloner.
- **Existing_Pipeline**: The current `trace-simulations/` components reused by this feature: `extract.py` (dataset metadata), `clone.py` (clone/checkout), `diffparse.py` (unified-diff parsing to per-file hunks with line numbers), and `summarize.py` (LLM endpoint call with fallback).
- **LLM_Endpoint**: The OpenAI-compatible CreateAI endpoint configured by `OPENAI_API_BASE` and `OPENAI_API_KEY` in the repo-root `.env`, called using the litellm `openai/` double-prefix convention described in `trace-simulations/README.md`.

## Requirements

### Requirement 1: Python code-structure extraction

**User Story:** As an HCI researcher, I want each Python source file decomposed into its modules, imports, constants, classes, and functions, so that the graph can show code relationships below the file level.

#### Acceptance Criteria

1. WHEN the Structure_Extractor processes a Python file in the Base_Commit_Sources, THE Structure_Extractor SHALL parse it with the standard-library `ast` module into a module Node.
2. WHEN the Structure_Extractor parses a module, THE Structure_Extractor SHALL extract module-level import statements as `import` Nodes, module-level constant/assignment targets as `const` Nodes, top-level classes as `class` Nodes, and top-level functions as `function` Nodes.
3. WHEN the Structure_Extractor extracts a class, THE Structure_Extractor SHALL extract that class's methods as `function` Nodes nested under the `class` Node.
4. WHEN the Structure_Extractor extracts a Symbol, THE Structure_Extractor SHALL record the Symbol's name and its start and end line numbers in the source file.
5. IF a Python file cannot be parsed by `ast` (for example, a syntax error on the pinned revision), THEN THE Structure_Extractor SHALL degrade that file to a file-level module Node without sub-symbols and continue processing the remaining files.
6. WHERE a file in the Base_Commit_Sources is not a Python file, THE Structure_Extractor SHALL represent it as a file-level Node without sub-symbols.

### Requirement 2: Change mapping and propagation

**User Story:** As an HCI researcher, I want each change mapped to the specific symbol it touches and reflected at every level above it, so that I can see which directories, modules, classes, and functions changed.

#### Acceptance Criteria

1. WHEN the Graph_Builder processes a Layer's parsed hunks for a file, THE Graph_Builder SHALL map each changed line to the innermost Symbol Node whose line range contains that line.
2. WHEN a changed line falls within a file but outside any extracted Symbol, THE Graph_Builder SHALL attribute the change to the enclosing module Node.
3. WHEN the Graph_Builder attributes a change to a Node, THE Graph_Builder SHALL record which Layer or Layers changed that Node and the added-line and removed-line counts per Layer.
4. WHEN a Node is directly changed, THE Graph_Builder SHALL mark every ancestor Node up to the root as a Changed_Node carrying the aggregated change of its descendants.
5. WHEN the Graph_Builder maps changes for a file whose module could not be decomposed into Symbols, THE Graph_Builder SHALL attribute all of that file's changes to the file-level module Node.
6. THE Graph_Builder SHALL keep the three Layers distinct in every Node's change annotation so the viewer can show them separately.

### Requirement 3: Multi-level graph document

**User Story:** As an HCI researcher, I want a single JSON document describing the full multi-level node hierarchy with change annotations and summaries, so that the viewer can render relationships and changes at varying levels of detail.

#### Acceptance Criteria

1. WHEN the Graph_Builder finishes processing the Target_Instance, THE Graph_Builder SHALL write a Graph_Document containing the root-to-leaf Node hierarchy spanning directory, module, import, const, class, and function levels.
2. WHEN the Graph_Builder records a Node, THE Graph_Builder SHALL include the Node's `kind`, name, stable identifier or path, child Nodes, change annotation, and a `changed` flag.
3. WHEN the Graph_Builder records a directly changed function or class Node, THE Graph_Builder SHALL include the source line range and the per-Layer hunks needed for the viewer's code/diff view.
4. THE Graph_Document SHALL identify, for every Node, whether the Node is unchanged, directly changed, or an ancestor of a changed Node, so the viewer can collapse unchanged subtrees by default.
5. THE Graph_Builder SHALL write the Graph_Document for the Target_Instance in place of the old `viz_data.json` shape and SHALL update the viewer manifest to reference it.

### Requirement 4: Per-level LLM summaries along changed paths

**User Story:** As an HCI researcher, I want a plain-language summary at every level of the changed paths, so that I understand what changed in each directory, module, class, and function without reading raw diffs.

#### Acceptance Criteria

1. WHEN the Level_Summarizer processes the Target_Instance, THE Level_Summarizer SHALL generate an LLM summary only for Nodes on a Changed_Path.
2. WHEN the Level_Summarizer summarizes a directly changed function or class Node, THE Level_Summarizer SHALL base the summary on that Node's diff hunks.
3. WHEN the Level_Summarizer summarizes a module or directory Node, THE Level_Summarizer SHALL base the summary on the summaries and aggregated changes of that Node's changed children.
4. THE Level_Summarizer SHALL NOT request a summary for any unchanged Node that is not on a Changed_Path.
5. WHEN the Level_Summarizer calls the LLM_Endpoint, THE Level_Summarizer SHALL read `OPENAI_API_BASE` and `OPENAI_API_KEY` from the repo-root `.env` and use the litellm `openai/` double-prefix model-name convention.
6. IF the LLM_Endpoint is unreachable or the credentials are unavailable, THEN THE Level_Summarizer SHALL record a fallback summary for the affected Nodes and continue, and SHALL record that fallbacks were used.
7. THE Level_Summarizer SHALL NOT write credential values into the Graph_Document or any other committed artifact.

### Requirement 5: On-demand collapsible graph viewer

**User Story:** As an HCI researcher, I want a graph that starts mostly collapsed and expands on demand along changed paths, so that I can grasp where changes live without being overwhelmed by thousands of nodes.

#### Acceptance Criteria

1. THE Graph_Viewer SHALL be a single self-contained static HTML file with inline vanilla JavaScript and inline CSS, rendering the graph with SVG, and SHALL reference no build tooling, external package dependency, or content delivery network resource.
2. WHEN the Graph_Viewer loads the Graph_Document, THE Graph_Viewer SHALL render the graph with unchanged subtrees collapsed by default and the Changed_Paths expanded to reveal the directly changed Nodes.
3. WHEN a user activates a collapsed Node, THE Graph_Viewer SHALL expand that Node by one Level to reveal its immediate children.
4. WHEN the Graph_Viewer renders a Node, THE Graph_Viewer SHALL visually highlight whether the Node is directly changed or an ancestor of a change, distinctly from unchanged Nodes, and SHALL distinguish the model, gold, and test Layers.
5. THE Graph_Viewer SHALL render Nodes as a node-link graph that expresses the containment relationships across directory, module, class, and function levels.
6. THE Graph_Viewer SHALL function when served from its containing folder via `python -m http.server`.

### Requirement 6: Code and diff detail view

**User Story:** As an HCI researcher, I want to see the actual code and diff of a selected node alongside the graph, so that I can connect a highlighted node to the concrete change.

#### Acceptance Criteria

1. WHEN a user selects a directly changed Node in the Graph_Viewer, THE Graph_Viewer SHALL display the Node's per-Layer diff hunks with added, removed, and context lines distinguished.
2. WHEN a user selects a Node that carries a summary, THE Graph_Viewer SHALL display that Node's natural-language summary.
3. WHEN a user selects a function or class Node, THE Graph_Viewer SHALL display the corresponding source lines of that Symbol for context alongside the diff.
4. WHERE a selected Node is an unchanged ancestor on a Changed_Path, THE Graph_Viewer SHALL display its summary and the aggregated change of its descendants.

### Requirement 7: Scope, environment, and reuse

**User Story:** As an HCI researcher, I want this prototype to run in the documented environment for one instance and reuse the existing pipeline, so that it is reproducible and consistent with the current tooling.

#### Acceptance Criteria

1. THE Change_Graph_Tooling SHALL build the Graph_Document for the Target_Instance `scikit-learn__scikit-learn-13496`.
2. THE Change_Graph_Tooling SHALL reuse the Existing_Pipeline components for dataset metadata, repository clone/checkout, unified-diff parsing, and the LLM endpoint call.
3. THE Change_Graph_Tooling SHALL place all Python scripts under the `trace-simulations/` directory and run on Windows using PowerShell in the `trace-sims` conda environment.
4. THE Change_Graph_Tooling SHALL perform structure extraction using only the Python standard library `ast` module for Python source files.
5. THE Change_Graph_Tooling SHALL read the LLM_Endpoint token from the repo-root `.env` file and SHALL exclude the token value from every file written to the repository.
