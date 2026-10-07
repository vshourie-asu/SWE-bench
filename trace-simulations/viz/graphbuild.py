#!/usr/bin/env python3
"""Graph_Builder: directory -> module -> symbol hierarchy with change overlay.

Implements Requirements 1.6, 2.x, 3.1-3.4, and 6.3 of the SWE-bench change-graph
feature. The builder walks a checked-out base-commit working tree into a nested
node hierarchy (``dir`` nodes and, per file, a ``module`` node whose children are
the symbols extracted by :mod:`structure`), then overlays the model/gold/test
change layers onto the innermost symbol each changed line touches, and finally
propagates change state and aggregated counts up to the root.

Line-number basis (critical correctness detail)
------------------------------------------------
The symbol line ranges come from parsing the file *as checked out at the base
commit* (pre-change). A unified diff's **old** side indexes exactly that source.
So changes are mapped using **old-side** line numbers: for each ``remove`` or
``context`` line we use ``LineChange.old_lineno``; for a pure ``add`` line (whose
``old_lineno`` is ``None``) we attribute it to the symbol containing the hunk's
old-side anchor (``Hunk.old_start``), falling back to the module node. Mixing up
the basis would mis-attribute changes, so the old side is used throughout.

The module uses only the Python standard library (Req 7.4) and reads ``.py`` files
as UTF-8 with ``errors="replace"`` so a stray byte never aborts the walk.
"""

from __future__ import annotations

from pathlib import Path

try:  # Package-relative import when loaded as ``viz.graphbuild``.
    from .structure import Symbol, extract_symbols
    from .diffparse import FileDiff, Hunk, LineChange, hunk_to_json
except ImportError:  # Standalone import when ``viz/`` is on ``sys.path`` directly.
    from structure import Symbol, extract_symbols
    from diffparse import FileDiff, Hunk, LineChange, hunk_to_json

__all__ = [
    "SKIP_DIRS",
    "build_graph",
]

# Directories pruned from the walk: VCS metadata, caches, tool state, and the
# JS dependency tree. Keeping this small avoids coupling to the old treebuild.
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    "node_modules",
    ".idea",
}

# Layers in a fixed, deterministic order so the document is stable across runs.
_LAYERS = ("model", "gold", "test")

# Files larger than this are treated as opaque modules (no symbol extraction);
# a trivial guard so a stray huge/binary ``.py`` never stalls the walk.
_MAX_SOURCE_BYTES = 2_000_000


# ---------------------------------------------------------------------------
# Task 2.1 -- directory / module / symbol scaffold
# ---------------------------------------------------------------------------


def _symbol_to_node(sym: Symbol, file_path: str, id_prefix: str) -> dict:
    """Convert a :class:`Symbol` (and its subtree) into a graph node.

    ``id_prefix`` is the parent's id; each symbol extends it with a
    ``::kind:name`` segment so ids are stable and unambiguous (Req 3.2).
    """
    node_id = f"{id_prefix}::{sym.kind}:{sym.name}"
    children = [_symbol_to_node(child, file_path, node_id) for child in sym.children]
    return _new_node(
        node_id=node_id,
        kind=sym.kind,
        name=sym.name,
        path=file_path,
        start=sym.start,
        end=sym.end,
        children=children,
    )


def _new_node(
    *,
    node_id: str,
    kind: str,
    name: str,
    path: str,
    children: list[dict],
    start: int | None = None,
    end: int | None = None,
) -> dict:
    """Create a graph node with the baseline change fields (Req 3.2, 3.4)."""
    node: dict = {
        "id": node_id,
        "kind": kind,
        "name": name,
        "path": path,
    }
    if start is not None:
        node["start"] = start
    if end is not None:
        node["end"] = end
    node["changed"] = False
    node["change_state"] = "none"
    node["annotations"] = {}
    node["children"] = children
    return node


def _read_source(file_path: Path) -> str | None:
    """Read a ``.py`` file as UTF-8 (``errors="replace"``); skip if oversized.

    Returns ``None`` when the file is too large to parse cheaply, so the caller
    emits a childless module node (Req 1.6-style degradation) rather than block.
    """
    try:
        if file_path.stat().st_size > _MAX_SOURCE_BYTES:
            return None
    except OSError:
        return None
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _build_module_node(file_path: Path, rel_path: str) -> dict:
    """Build a ``module`` node for one file.

    Python files get their symbol children from :func:`extract_symbols`; every
    other file (and any unreadable/oversized ``.py``) becomes a childless module
    node (Req 1.6). The module id is its forward-slash relative path.
    """
    if file_path.suffix == ".py":
        source = _read_source(file_path)
        if source is not None:
            module_sym = extract_symbols(source, rel_path)
            children = [
                _symbol_to_node(child, rel_path, rel_path)
                for child in module_sym.children
            ]
            return _new_node(
                node_id=rel_path,
                kind="module",
                name=file_path.name,
                path=rel_path,
                start=module_sym.start,
                end=module_sym.end,
                children=children,
            )
    return _new_node(
        node_id=rel_path,
        kind="module",
        name=file_path.name,
        path=rel_path,
        children=[],
    )


def _build_dir_node(dir_path: Path, repo_dir: Path) -> dict:
    """Recursively build a ``dir`` node for ``dir_path`` with sorted children.

    Subdirectories in ``SKIP_DIRS`` are pruned. Children are ordered
    deterministically: directories and files sorted by name (case-sensitive on
    the raw name) and interleaved by name so the tree is stable across runs.
    All paths are forward-slash relative to ``repo_dir``.
    """
    rel = dir_path.relative_to(repo_dir).as_posix()
    rel_path = "" if rel == "." else rel
    node_id = rel_path

    try:
        entries = list(dir_path.iterdir())
    except OSError:
        entries = []

    # Deterministic ordering by entry name; directories and files share the sort
    # key so the walk is reproducible regardless of filesystem iteration order.
    entries.sort(key=lambda p: p.name)

    children: list[dict] = []
    for entry in entries:
        if entry.is_dir():
            if entry.name in SKIP_DIRS:
                continue
            children.append(_build_dir_node(entry, repo_dir))
        elif entry.is_file():
            child_rel = entry.relative_to(repo_dir).as_posix()
            children.append(_build_module_node(entry, child_rel))

    name = dir_path.name if rel_path else ""
    return _new_node(
        node_id=node_id,
        kind="dir",
        name=name,
        path=rel_path,
        children=children,
    )


# ---------------------------------------------------------------------------
# Task 2.2 -- change mapping, annotation, propagation
# ---------------------------------------------------------------------------


def _index_modules(root: dict) -> dict[str, dict]:
    """Map each file path to its ``module`` node for O(1) layer attribution."""
    modules: dict[str, dict] = {}

    def walk(node: dict) -> None:
        if node["kind"] == "module":
            modules[node["path"]] = node
        for child in node["children"]:
            walk(child)

    walk(root)
    return modules


def _innermost_symbol(module: dict, lineno: int) -> dict:
    """Return the innermost descendant symbol of ``module`` containing ``lineno``.

    Recursive descent: among children whose ``[start, end]`` contains the line,
    pick the first match and descend; symbols without ranges (none under a
    module) are ignored. Falls back to the module node when no symbol contains
    the line (Req 2.2).
    """
    current = module
    while True:
        match: dict | None = None
        for child in current["children"]:
            start = child.get("start")
            end = child.get("end")
            if start is None or end is None:
                continue
            if start <= lineno <= end:
                match = child
                break
        if match is None:
            return current
        current = match


def _record_change(
    node: dict,
    layer: str,
    *,
    added: int,
    removed: int,
) -> dict:
    """Accrue per-layer added/removed counts on ``node``, keeping layers separate.

    Returns the layer's annotation entry so the caller can attach ``hunks`` and a
    ``source`` slice to it (Req 2.3, 2.6).
    """
    annotations = node["annotations"]
    entry = annotations.get(layer)
    if entry is None:
        entry = {"added": 0, "removed": 0}
        annotations[layer] = entry
    entry["added"] += added
    entry["removed"] += removed
    return entry


def _line_counts(lines: list[LineChange]) -> tuple[int, int]:
    """Return (added, removed) counts for a list of line changes."""
    added = sum(1 for ln in lines if ln.kind == "add")
    removed = sum(1 for ln in lines if ln.kind == "remove")
    return added, removed


def _target_for_line(module: dict, line: LineChange, hunk: Hunk) -> dict:
    """Pick the leaf node a single changed line maps to (old-side basis).

    ``remove``/``context`` lines carry an ``old_lineno`` that indexes the base
    source; pure ``add`` lines (``old_lineno is None``) are attributed to the
    symbol containing the hunk's old-side anchor (``Hunk.old_start``), falling
    back to the module when nothing contains it (Req 2.1, 2.2).
    """
    old_lineno = line.old_lineno
    if old_lineno is None:
        old_lineno = hunk.old_start
    return _innermost_symbol(module, old_lineno)


def _source_slice(source_lines: list[str], node: dict) -> str:
    """Return the node's base-commit source lines sliced ``start..end`` inclusive."""
    start = node.get("start")
    end = node.get("end")
    if start is None or end is None:
        return ""
    # ``start``/``end`` are 1-based inclusive; clamp to the available lines.
    lo = max(1, start)
    hi = min(len(source_lines), end)
    if hi < lo:
        return ""
    return "\n".join(source_lines[lo - 1 : hi])


def _apply_layer(
    module: dict,
    file_diff: FileDiff,
    layer: str,
    source_lines: list[str],
) -> None:
    """Map one layer's hunks for one file onto ``module``'s symbol subtree.

    Each changed line is routed to its innermost containing node; counts and the
    hunk are recorded per target node. ``hunks`` (and, for class/function nodes,
    a ``source`` slice) are stored only on the direct leaf the hunk maps to
    (Req 2.3, 3.3, 6.3). A module with no symbols absorbs all changes (Req 2.5).
    """
    for hunk in file_diff.hunks:
        # Group this hunk's lines by the leaf node they map to, so a single hunk
        # that straddles symbols still records per-target counts correctly while
        # the raw hunk is attached to each touched direct leaf.
        buckets: dict[int, tuple[dict, list[LineChange]]] = {}
        order: list[int] = []
        for line in hunk.lines:
            if line.kind == "context":
                continue
            target = _target_for_line(module, line, hunk)
            key = id(target)
            if key not in buckets:
                buckets[key] = (target, [])
                order.append(key)
            buckets[key][1].append(line)

        if not order:
            # A context-only hunk (should be rare): anchor to the hunk's old side.
            target = _innermost_symbol(module, hunk.old_start)
            buckets[id(target)] = (target, [])
            order.append(id(target))

        for key in order:
            target, lines = buckets[key]
            added, removed = _line_counts(lines)
            entry = _record_change(target, layer, added=added, removed=removed)
            # Attach the raw hunk to the direct leaf (modules may carry hunks too,
            # per the task note); ancestors never receive raw hunks.
            entry.setdefault("hunks", []).append(hunk_to_json(hunk))
            # Store the base-commit source slice only on class/function nodes.
            if target["kind"] in ("class", "function") and "source" not in target:
                sliced = _source_slice(source_lines, target)
                if sliced:
                    target["source"] = sliced


def _map_layer(
    modules: dict[str, dict],
    repo_dir: Path,
    diffs: list[FileDiff],
    layer: str,
) -> None:
    """Attribute every file diff in one layer to its module's symbol subtree."""
    for file_diff in diffs:
        module = modules.get(file_diff.path)
        if module is None:
            # The diff names a path not present in the walked tree (e.g. a file
            # created by the patch, or outside the subtree). Nothing to map onto.
            continue
        source_lines: list[str] = []
        if module["kind"] == "module" and module["path"].endswith(".py"):
            file_path = repo_dir / Path(module["path"])
            source = _read_source(file_path)
            if source is not None:
                source_lines = source.splitlines()
        _apply_layer(module, file_diff, layer, source_lines)


def _propagate(node: dict) -> dict[str, dict[str, int]]:
    """Post-order pass: set change_state/changed and aggregate counts upward.

    Returns the aggregated per-layer ``{added, removed}`` totals contributed by
    ``node`` and its subtree. A node is ``direct`` when it carries its own
    annotations, ``ancestor`` when any descendant changed, else ``none`` (Req
    2.4, 3.4). Ancestors receive summed counts under their ``annotations`` but
    never the raw hunks of their descendants.
    """
    # Totals contributed by descendants, summed per layer.
    descendant_totals: dict[str, dict[str, int]] = {}
    any_descendant_changed = False

    for child in node["children"]:
        child_totals = _propagate(child)
        if child["change_state"] != "none":
            any_descendant_changed = True
        for layer, counts in child_totals.items():
            agg = descendant_totals.setdefault(layer, {"added": 0, "removed": 0})
            agg["added"] += counts["added"]
            agg["removed"] += counts["removed"]

    is_direct = bool(node["annotations"])

    if is_direct:
        node["change_state"] = "direct"
    elif any_descendant_changed:
        node["change_state"] = "ancestor"
    else:
        node["change_state"] = "none"
    node["changed"] = node["change_state"] != "none"

    # This node's own per-layer totals (direct leaves) feed the parent's sums.
    own_totals: dict[str, dict[str, int]] = {}
    for layer, entry in node["annotations"].items():
        own_totals[layer] = {
            "added": int(entry.get("added", 0)),
            "removed": int(entry.get("removed", 0)),
        }

    # Combine own + descendant totals for the value returned to the parent.
    combined: dict[str, dict[str, int]] = {}
    for layer in set(own_totals) | set(descendant_totals):
        own = own_totals.get(layer, {"added": 0, "removed": 0})
        desc = descendant_totals.get(layer, {"added": 0, "removed": 0})
        combined[layer] = {
            "added": own["added"] + desc["added"],
            "removed": own["removed"] + desc["removed"],
        }

    # Ancestors (nodes without their own annotations but with changed
    # descendants) record the summed per-layer counts so the viewer can show
    # aggregated change without the raw hunks (Req 3.4, 6.4).
    if not is_direct and any_descendant_changed:
        for layer, counts in descendant_totals.items():
            node["annotations"][layer] = {
                "added": counts["added"],
                "removed": counts["removed"],
            }

    return combined


def build_graph(
    repo_dir: Path,
    model: list[FileDiff],
    gold: list[FileDiff],
    test: list[FileDiff],
) -> dict:
    """Build the Graph_Document root node for a checked-out working tree.

    Walks ``repo_dir`` into the directory/module/symbol scaffold (task 2.1),
    overlays the three change layers using old-side line numbers, and propagates
    change state and aggregated counts to the root (task 2.2).

    Parameters
    ----------
    repo_dir:
        The repository working tree checked out at the base commit.
    model, gold, test:
        Per-layer lists of :class:`FileDiff` from ``diffparse.parse_patch``.

    Returns the root ``dir`` node of the hierarchy.
    """
    repo_dir = Path(repo_dir)
    root = _build_dir_node(repo_dir, repo_dir)

    modules = _index_modules(root)
    layer_diffs = {"model": model, "gold": gold, "test": test}
    for layer in _LAYERS:
        _map_layer(modules, repo_dir, layer_diffs[layer], layer)

    _propagate(root)
    return root
