"""Unified-diff parser for the SWE-bench change visualization tooling.

Parses unified diffs (model, gold, and test patches) into a small set of
dataclasses that mirror the ``viz_data.json`` annotation schema described in
the design document. The parser prefers the third-party ``unidiff`` package
when it is importable and otherwise falls back to a standard-library,
line-oriented state machine that understands ``diff --git`` sections,
``---``/``+++`` file headers, and ``@@ ... @@`` hunk headers.

Paths are normalized to use forward slashes with the leading ``a/`` and ``b/``
prefixes stripped so that annotation paths line up with the forward-slash tree
keys produced on Windows. File creations and deletions expressed via
``/dev/null`` resolve to the non-null side as the file path.

All I/O is UTF-8 and the module has no required third-party dependency: the
``unidiff`` import is attempted lazily and the stdlib fallback covers every
case the pipeline needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "LineChange",
    "Hunk",
    "FileDiff",
    "parse_patch",
    "line_to_json",
    "hunk_to_json",
    "file_diff_to_json",
]

# @@ -old_start[,old_len] +new_start[,new_len] @@[ optional section heading]
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_len>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))? @@(?P<section>.*)$"
)


@dataclass
class LineChange:
    """A single classified line within a hunk.

    ``kind`` is exactly one of ``"add"``, ``"remove"``, or ``"context"``.
    ``old_lineno`` is ``None`` for added lines; ``new_lineno`` is ``None`` for
    removed lines; context lines carry both.
    """

    kind: str
    content: str
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class Hunk:
    """A contiguous block of changes within one file."""

    old_start: int
    new_start: int
    header: str
    lines: list[LineChange] = field(default_factory=list)


@dataclass
class FileDiff:
    """All hunks and summary counts for a single changed file.

    ``path`` is the normalized (forward-slash, prefix-stripped) new path, or
    the old path for a deletion where the new side is ``/dev/null``.
    """

    path: str
    added: int
    removed: int
    hunks: list[Hunk] = field(default_factory=list)


def _normalize_path(raw: str) -> str:
    """Strip ``a/``/``b/`` prefixes and quoting; return a forward-slash path."""
    path = raw.strip()
    # Drop a trailing tab-delimited timestamp sometimes present in ---/+++ lines.
    if "\t" in path:
        path = path.split("\t", 1)[0]
    # git may quote paths containing special characters with surrounding quotes.
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path.replace("\\", "/")


def _is_dev_null(raw: str) -> bool:
    return raw.strip().split("\t", 1)[0] in ("/dev/null", "a/dev/null", "b/dev/null")


def _resolve_path(old_raw: str | None, new_raw: str | None, git_fallback: str | None) -> str:
    """Pick the file path, using the non-null side for create/delete diffs."""
    new_null = new_raw is None or _is_dev_null(new_raw)
    old_null = old_raw is None or _is_dev_null(old_raw)
    if new_raw is not None and not new_null:
        return _normalize_path(new_raw)
    if old_raw is not None and not old_null:
        return _normalize_path(old_raw)
    if git_fallback is not None:
        return git_fallback
    # Both sides /dev/null (should not happen) -> empty string.
    return ""


def parse_patch(patch_text: str) -> list[FileDiff]:
    """Parse a unified diff into a list of :class:`FileDiff`.

    Uses ``unidiff`` when importable, otherwise a stdlib fallback parser.
    """
    if not patch_text:
        return []
    try:
        from unidiff import PatchSet  # noqa: F401  (import presence is the signal)
    except ImportError:
        return _parse_fallback(patch_text)
    return _parse_with_unidiff(patch_text)


def _parse_with_unidiff(patch_text: str) -> list[FileDiff]:
    """Parse using the ``unidiff`` package, mapping into our dataclasses."""
    from unidiff import PatchSet

    patch_set = PatchSet(patch_text)
    results: list[FileDiff] = []
    for pf in patch_set:
        source = getattr(pf, "source_file", None)
        target = getattr(pf, "target_file", None)
        path = _resolve_path(source, target, None)
        hunks: list[Hunk] = []
        added = 0
        removed = 0
        for h in pf:
            header = f"@@ -{h.source_start},{h.source_length} +{h.target_start},{h.target_length} @@"
            section = getattr(h, "section_header", "") or ""
            if section:
                header = f"{header} {section}"
            hunk = Hunk(old_start=h.source_start, new_start=h.target_start, header=header)
            for line in h:
                content = line.value.rstrip("\n")
                if line.is_added:
                    hunk.lines.append(
                        LineChange("add", content, None, line.target_line_no)
                    )
                    added += 1
                elif line.is_removed:
                    hunk.lines.append(
                        LineChange("remove", content, line.source_line_no, None)
                    )
                    removed += 1
                elif line.is_context:
                    hunk.lines.append(
                        LineChange(
                            "context", content, line.source_line_no, line.target_line_no
                        )
                    )
                # Skip "\ No newline at end of file" markers (no diff line type).
            hunks.append(hunk)
        results.append(FileDiff(path=path, added=added, removed=removed, hunks=hunks))
    return results


def _parse_fallback(patch_text: str) -> list[FileDiff]:
    """Standard-library line-oriented unified-diff parser."""
    results: list[FileDiff] = []

    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_lineno = 0
    new_lineno = 0

    # Pending header state collected between a `diff --git` and its first hunk.
    git_path: str | None = None
    minus_path: str | None = None
    plus_path: str | None = None

    def finalize_file() -> None:
        nonlocal current, hunk
        if current is not None:
            if hunk is not None:
                current.hunks.append(hunk)
                hunk = None
            results.append(current)
            current = None

    def start_file() -> FileDiff:
        path = _resolve_path(minus_path, plus_path, git_path)
        return FileDiff(path=path, added=0, removed=0, hunks=[])

    lines = patch_text.splitlines()
    for raw in lines:
        if raw.startswith("diff --git"):
            # Close out any file in progress, reset header state.
            finalize_file()
            git_path = None
            minus_path = None
            plus_path = None
            # Try to recover a path directly from the `diff --git a/x b/x` line.
            parts = raw.split(" ")
            if len(parts) >= 4:
                git_path = _normalize_path(parts[-1])
            continue

        if raw.startswith("--- "):
            minus_path = raw[4:]
            continue

        if raw.startswith("+++ "):
            plus_path = raw[4:]
            # A fresh file header establishes the current file.
            finalize_file()
            current = start_file()
            hunk = None
            continue

        m = _HUNK_HEADER_RE.match(raw)
        if m is not None:
            if current is None:
                # Hunk without a preceding +++ header: synthesize from git_path.
                current = start_file()
            if hunk is not None:
                current.hunks.append(hunk)
            old_start = int(m.group("old_start"))
            new_start = int(m.group("new_start"))
            section = m.group("section") or ""
            header = raw.rstrip("\n")
            hunk = Hunk(old_start=old_start, new_start=new_start, header=header)
            old_lineno = old_start
            new_lineno = new_start
            continue

        if hunk is None or current is None:
            # Index lines, mode lines, "\ No newline", or preamble: ignore.
            continue

        if raw.startswith("+"):
            content = raw[1:]
            hunk.lines.append(LineChange("add", content, None, new_lineno))
            current.added += 1
            new_lineno += 1
        elif raw.startswith("-"):
            content = raw[1:]
            hunk.lines.append(LineChange("remove", content, old_lineno, None))
            current.removed += 1
            old_lineno += 1
        elif raw.startswith("\\"):
            # "\ No newline at end of file" — not a content line.
            continue
        elif raw.startswith(" ") or raw == "":
            content = raw[1:] if raw.startswith(" ") else ""
            hunk.lines.append(LineChange("context", content, old_lineno, new_lineno))
            old_lineno += 1
            new_lineno += 1
        else:
            # Unknown line type inside a hunk: treat as context defensively.
            hunk.lines.append(LineChange("context", raw, old_lineno, new_lineno))
            old_lineno += 1
            new_lineno += 1

    finalize_file()
    return results


def line_to_json(line: LineChange) -> dict:
    """Serialize a :class:`LineChange` to the viz_data.json line shape."""
    return {
        "kind": line.kind,
        "content": line.content,
        "old_lineno": line.old_lineno,
        "new_lineno": line.new_lineno,
    }


def hunk_to_json(hunk: Hunk) -> dict:
    """Serialize a :class:`Hunk` to the viz_data.json hunk shape."""
    return {
        "old_start": hunk.old_start,
        "new_start": hunk.new_start,
        "header": hunk.header,
        "lines": [line_to_json(line) for line in hunk.lines],
    }


def file_diff_to_json(file_diff: FileDiff) -> dict:
    """Serialize a :class:`FileDiff` to the viz_data.json annotation shape."""
    return {
        "path": file_diff.path,
        "added": file_diff.added,
        "removed": file_diff.removed,
        "hunks": [hunk_to_json(h) for h in file_diff.hunks],
    }
