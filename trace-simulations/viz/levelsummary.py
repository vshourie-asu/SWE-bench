#!/usr/bin/env python3
"""Level_Summarizer: per-level natural-language summaries along changed paths.

Implements Requirement 4 of the SWE-bench change-graph feature. Given a
Graph_Document root node (the directory tree produced by :mod:`graphbuild`), this
module attaches a one-sentence ``summary`` to every node on a Changed_Path -- that
is, every node whose ``change_state`` is not ``"none"`` -- and skips every unchanged
node entirely (Req 4.1, 4.4). The walk is post-order so a parent's rollup summary
can consume its children's just-computed summaries (Req 4.3).

Two summary flavors
-------------------
- **Leaf change** (Req 4.2): a ``direct`` ``function``/``class``/``module`` node is
  summarized from its own change -- a combined unified-diff text built from the raw
  hunks stored under ``annotations[layer].hunks`` (headers + classified lines with
  ``+``/``-``/space prefixes). For a ``direct`` class/function the node's ``source``
  slice is appended as brief extra context.
- **Rollup** (Req 4.3): a ``dir``/``module`` node (or a ``class`` acting purely as an
  ancestor of changed methods) is summarized from a compact rollup of its *changed*
  children -- each child's kind+name, its just-computed summary, and the aggregated
  per-layer added/removed counts.

Both flavors go through the reused :func:`summarize.summarize_file` endpoint helper
(the rollup passes its rollup text as the ``diff_text`` argument with a descriptive
path label), so there is exactly one place that talks to the LLM_Endpoint.

Endpoint, fallback, and secret hygiene (Req 4.5, 4.6, 4.7, 7.5)
--------------------------------------------------------------
Credentials are read once via :func:`summarize.load_credentials` and the litellm
``openai/`` double-prefix model defaults to :data:`summarize.DEFAULT_MODEL`. On ANY
failure (missing credentials, endpoint error, timeout, malformed response) a
deterministic templated fallback is recorded instead and the node is still given a
non-empty summary. Leaf fallbacks reuse :func:`summarize.fallback_summary` from the
aggregated counts; rollup fallbacks use a short templated sentence from the counts
and the changed-child count. The credential token is never written into any node and
never printed -- a fallback is reported only by its exception *type* as
``  [fallback] <id>: <ExceptionType>``.

Signaling to the orchestrator
------------------------------
:func:`summarize_graph` RETURNS a bool ``used_llm_everywhere`` that is ``True`` iff
at least one node was summarized and every summary came from the endpoint (no
fallback used). The orchestrator (task 5) uses this return value to set the
Graph_Document's top-level ``generated_with_llm`` flag.

Standard library only (Req 7.4); UTF-8; sibling import via the relative-then-absolute
pattern used across the ``viz`` package.
"""

from __future__ import annotations

from pathlib import Path

try:  # Package-relative import when loaded as ``viz.levelsummary``.
    from .summarize import (
        DEFAULT_MODEL,
        fallback_summary,
        load_credentials,
        summarize_file,
    )
except ImportError:  # Standalone import when ``viz/`` is on ``sys.path`` directly.
    from summarize import (
        DEFAULT_MODEL,
        fallback_summary,
        load_credentials,
        summarize_file,
    )

__all__ = [
    "summarize_graph",
]

# Node kinds summarized directly from their own diff hunks (leaf-change flavor).
_LEAF_KINDS = ("module", "class", "function")

# Keep an appended source slice short so the user prompt stays concise.
_SOURCE_CONTEXT_LINES = 40


def _combined_diff_text(node: dict) -> str:
    """Build a combined unified-diff-ish text from a node's stored hunks.

    Concatenates every layer's hunk headers and classified lines (``+`` for add,
    ``-`` for remove, space for context) so the endpoint sees the full change for
    the node regardless of which layer(s) touched it. Returns an empty string when
    the node carries no raw hunks (e.g. an ancestor), signalling the caller to use
    the rollup flavor instead.
    """
    annotations = node.get("annotations") or {}
    sections: list[str] = []
    for layer in sorted(annotations):
        layer_ann = annotations[layer] or {}
        hunks = layer_ann.get("hunks") or []
        if not hunks:
            continue
        lines_out: list[str] = [f"# layer: {layer}"]
        for hunk in hunks:
            header = hunk.get("header")
            if header:
                lines_out.append(header)
            for line in hunk.get("lines") or []:
                kind = line.get("kind")
                content = line.get("content", "")
                prefix = {"add": "+", "remove": "-", "context": " "}.get(kind, " ")
                lines_out.append(f"{prefix}{content}")
        sections.append("\n".join(lines_out))
    return "\n".join(sections)


def _aggregated_counts(node: dict) -> tuple[int, int]:
    """Sum ``added``/``removed`` across every layer annotation of ``node``."""
    added = 0
    removed = 0
    for layer_ann in (node.get("annotations") or {}).values():
        if not layer_ann:
            continue
        added += int(layer_ann.get("added", 0) or 0)
        removed += int(layer_ann.get("removed", 0) or 0)
    return added, removed


def _per_layer_counts_text(node: dict) -> str:
    """Render a node's per-layer added/removed counts as a compact one-liner."""
    annotations = node.get("annotations") or {}
    parts: list[str] = []
    for layer in sorted(annotations):
        layer_ann = annotations[layer] or {}
        added = int(layer_ann.get("added", 0) or 0)
        removed = int(layer_ann.get("removed", 0) or 0)
        parts.append(f"{layer} +{added}/-{removed}")
    return ", ".join(parts)


def _node_label(node: dict) -> str:
    """A short, descriptive path label for a node (used as the endpoint's path)."""
    kind = node.get("kind", "node")
    name = node.get("name") or node.get("path") or node.get("id") or ""
    if name:
        return f"{kind} {name}"
    return kind


def _changed_children(node: dict) -> list[dict]:
    """Return ``node``'s direct children on a Changed_Path (change_state != none)."""
    return [
        child
        for child in node.get("children") or []
        if child.get("change_state", "none") != "none"
    ]


def _rollup_text(node: dict) -> str:
    """Build the rollup context passed to the endpoint for a dir/module/class node.

    Describes the node and each of its changed children by kind+name, the child's
    just-computed summary, and that child's aggregated per-layer counts, so the
    endpoint can synthesize one overall sentence (Req 4.3). This text is handed to
    :func:`summarize.summarize_file` as its ``diff_text`` argument.
    """
    lines: list[str] = [
        f"{_node_label(node)} contains the following changed children.",
        "Summarize the overall change across these children in one sentence.",
        "",
    ]
    for child in _changed_children(node):
        child_summary = child.get("summary") or "(no summary)"
        counts = _per_layer_counts_text(child) or "no counted lines"
        lines.append(
            f"- {child.get('kind', 'node')} {child.get('name', '')}"
            f" [{counts}]: {child_summary}"
        )
    return "\n".join(lines)


def _rollup_fallback(node: dict) -> str:
    """Deterministic templated rollup summary from counts + changed-child count."""
    added, removed = _aggregated_counts(node)
    child_count = len(_changed_children(node))
    label = node.get("name") or node.get("path") or node.get("kind", "node")
    noun = "changed child" if child_count == 1 else "changed children"
    return (
        f"{label}: {child_count} {noun}, "
        f"{added} line(s) added, {removed} line(s) removed."
    )


def _is_leaf_change(node: dict) -> bool:
    """True when the node should be summarized from its own diff hunks.

    A ``direct`` function/class/module node that actually carries raw hunks is a
    leaf change. A ``class`` that only acts as an ancestor of changed methods (no
    hunks of its own) is handled by the rollup flavor instead.
    """
    if node.get("change_state") != "direct":
        return False
    if node.get("kind") not in _LEAF_KINDS:
        return False
    return bool(_combined_diff_text(node))


def _summarize_node(
    node: dict,
    base: str | None,
    key: str | None,
    model: str,
) -> bool:
    """Attach a ``summary`` to one changed node; return True iff the endpoint was used.

    Chooses the leaf-change or rollup flavor, calls the reused endpoint helper, and
    on ANY failure records a deterministic templated fallback and reports the
    exception type (never the token). The node is always left with a non-empty
    ``summary``.
    """
    label = _node_label(node)

    if _is_leaf_change(node):
        diff_text = _combined_diff_text(node)
        # For a direct class/function, append a short source slice for context.
        if node.get("kind") in ("class", "function") and node.get("source"):
            source = node["source"]
            snippet = "\n".join(source.splitlines()[:_SOURCE_CONTEXT_LINES])
            diff_text = f"{diff_text}\n\n# source context:\n{snippet}"
    else:
        diff_text = _rollup_text(node)

    try:
        node["summary"] = summarize_file(label, diff_text, base, key, model)
        return True
    except Exception as exc:  # missing creds, endpoint down, timeout, bad response...
        if _is_leaf_change(node):
            added, removed = _aggregated_counts(node)
            node["summary"] = fallback_summary(label, added, removed)
        else:
            node["summary"] = _rollup_fallback(node)
        # Report by exception type only -- never the token or endpoint response.
        print(f"  [fallback] {node.get('id', label)}: {type(exc).__name__}")
        return False


def summarize_graph(
    root: dict,
    env_path: Path,
    model: str = DEFAULT_MODEL,
) -> bool:
    """Attach a ``summary`` to every changed-path node of ``root``, bottom-up.

    Performs a post-order walk so children are summarized before their parents
    (Req 4.3). A node is summarized if and only if its ``change_state`` is not
    ``"none"`` (Req 4.1, 4.4): ``direct`` leaves from their own combined diff hunks
    (Req 4.2), and ``dir``/``module``/ancestor-``class`` nodes from a rollup of their
    changed children (Req 4.3).

    Credentials are read once from ``env_path`` via :func:`summarize.load_credentials`
    (Req 4.5). On ANY failure or when credentials are absent, a deterministic
    templated fallback is recorded and the node still carries a non-empty summary
    (Req 4.6). The credential token is never written into the graph or printed
    (Req 4.7, 7.5).

    Returns ``used_llm_everywhere``: ``True`` iff at least one node was summarized and
    every summary came from the endpoint (no fallback). The orchestrator uses this to
    set the Graph_Document's top-level ``generated_with_llm`` flag.
    """
    base, key = load_credentials(env_path)

    summarized_any = False
    used_llm_everywhere = True

    def visit(node: dict) -> None:
        nonlocal summarized_any, used_llm_everywhere
        # Post-order: summarize children first so a parent can roll them up.
        for child in node.get("children") or []:
            visit(child)
        if node.get("change_state", "none") == "none":
            return
        summarized_any = True
        if not _summarize_node(node, base, key, model):
            used_llm_everywhere = False

    visit(root)

    # True only when there was at least one changed node and none fell back.
    return bool(summarized_any and used_llm_everywhere)
