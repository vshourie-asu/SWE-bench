#!/usr/bin/env python3
"""Structure_Extractor: Python source -> a symbol hierarchy via the stdlib ``ast``.

Implements Requirement 1 of the SWE-bench change-graph feature. Given the text of
one Python source file (as checked out at the base commit) this module parses it
with the standard-library :mod:`ast` module into a tree of :class:`Symbol` nodes:
a top-level ``module`` symbol whose children are the module-level imports, the
module-level constants/assignments, the top-level classes (each with its methods
nested as ``function`` symbols), and the top-level functions.

Each symbol records 1-based inclusive ``start``/``end`` line numbers taken from
``node.lineno``/``node.end_lineno`` (available since Python 3.8; the ``trace-sims``
env is 3.11). Decorator lines are folded into a symbol's ``start`` so a decorated
class or function's range begins at its first decorator (Req 1.4).

Degradation (Req 1.5)
---------------------
If the source cannot be parsed -- a ``SyntaxError`` on the pinned revision, or any
other parse-time failure -- :func:`extract_symbols` returns a childless ``module``
symbol spanning the whole file rather than raising, so the caller can still place a
file-level node in the graph and keep processing the remaining files. Non-Python
files never reach this module; the Graph_Builder represents them directly as plain
file nodes (Req 1.6).

The module uses only the Python standard library (Req 7.4) and performs no I/O: the
caller passes the already-read source text so the component stays pure and testable.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

__all__ = [
    "Symbol",
    "extract_symbols",
    "symbol_to_dict",
]


@dataclass
class Symbol:
    """One extracted code element in the module symbol hierarchy.

    ``kind`` is exactly one of ``"module"``, ``"import"``, ``"const"``,
    ``"class"``, or ``"function"``. ``start`` and ``end`` are 1-based inclusive
    source line numbers. ``children`` holds nested symbols (a module's
    imports/consts/classes/functions, or a class's methods).
    """

    kind: str
    name: str
    start: int
    end: int
    children: list["Symbol"] = field(default_factory=list)


def _node_start(node: ast.AST) -> int:
    """Return a node's 1-based start line, folding in any decorator lines.

    For a decorated class/function the earliest decorator line precedes
    ``node.lineno``, so the symbol's range begins at the first decorator (Req 1.4).
    """
    lineno = getattr(node, "lineno", 1)
    decorators = getattr(node, "decorator_list", None) or []
    for decorator in decorators:
        dec_lineno = getattr(decorator, "lineno", None)
        if dec_lineno is not None and dec_lineno < lineno:
            lineno = dec_lineno
    return lineno


def _node_end(node: ast.AST, default: int) -> int:
    """Return a node's 1-based inclusive end line, defaulting when unavailable."""
    end = getattr(node, "end_lineno", None)
    if end is None:
        return default
    return end


def _import_name(node: ast.AST) -> str:
    """Build a readable name for an ``import`` / ``from ... import`` statement.

    For ``import a, b.c`` -> ``"a, b.c"``; for ``from m import x, y`` -> ``"m"``.
    The module side of a ``from`` import is the most useful label for the graph.
    """
    if isinstance(node, ast.Import):
        return ", ".join(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        # ``node.module`` is None for ``from . import x``; render the dots instead.
        prefix = "." * (node.level or 0)
        return f"{prefix}{node.module or ''}" or ", ".join(
            alias.name for alias in node.names
        )
    return ""


def _assign_const_names(node: ast.AST) -> list[str]:
    """Return the Name targets of a module-level assignment, else an empty list.

    ``ast.Assign`` may bind several targets (``A = B = 1``); only plain ``Name``
    targets become ``const`` symbols. Tuple/attribute/subscript targets are
    skipped. ``ast.AnnAssign`` carries a single target.
    """
    names: list[str] = []
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.append(target.id)
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def _class_children(class_node: ast.ClassDef) -> list[Symbol]:
    """Extract a class's methods as nested ``function`` symbols (Req 1.3)."""
    methods: list[Symbol] = []
    for child in class_node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = _node_start(child)
            methods.append(
                Symbol(
                    kind="function",
                    name=child.name,
                    start=start,
                    end=_node_end(child, start),
                    children=[],
                )
            )
    return methods


def extract_symbols(source: str, module_name: str) -> Symbol:
    """Parse ``source`` into a ``module`` :class:`Symbol` hierarchy.

    Extracts module-level imports (``import``), module-level constant/assignment
    targets (``const``), top-level classes (``class``, with their methods nested
    as ``function`` symbols), and top-level functions (``function``) in source
    order (Req 1.1, 1.2, 1.3). Every symbol carries 1-based inclusive line ranges
    including decorator lines (Req 1.4).

    On a ``SyntaxError`` or any other parse failure the function returns a
    childless ``module`` symbol spanning line 1 to the file's last line (at least
    1), so an unparseable file still yields a file-level node (Req 1.5).
    """
    line_count = max(1, len(source.splitlines()))

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        # ``ValueError`` covers e.g. source containing null bytes; ``RecursionError``
        # covers pathologically nested input. Any parse failure degrades gracefully.
        return Symbol(kind="module", name=module_name, start=1, end=line_count, children=[])

    module_end = _node_end(tree, line_count)
    if isinstance(getattr(tree, "body", None), list) and tree.body:
        last = tree.body[-1]
        module_end = max(module_end, _node_end(last, module_end))

    children: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            start = _node_start(node)
            children.append(
                Symbol(
                    kind="import",
                    name=_import_name(node),
                    start=start,
                    end=start,
                    children=[],
                )
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            start = _node_start(node)
            for name in _assign_const_names(node):
                children.append(
                    Symbol(
                        kind="const",
                        name=name,
                        start=start,
                        end=start,
                        children=[],
                    )
                )
        elif isinstance(node, ast.ClassDef):
            start = _node_start(node)
            children.append(
                Symbol(
                    kind="class",
                    name=node.name,
                    start=start,
                    end=_node_end(node, start),
                    children=_class_children(node),
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = _node_start(node)
            children.append(
                Symbol(
                    kind="function",
                    name=node.name,
                    start=start,
                    end=_node_end(node, start),
                    children=[],
                )
            )

    return Symbol(
        kind="module",
        name=module_name,
        start=1,
        end=max(1, module_end),
        children=children,
    )


def symbol_to_dict(sym: Symbol) -> dict:
    """Serialize a :class:`Symbol` tree to a plain nested ``dict``.

    A minimal helper for the Graph_Builder: mirrors the dataclass fields and
    recurses into ``children``. Kept intentionally small -- the builder owns the
    richer node shape (ids, annotations, change state) layered on top of this.
    """
    return {
        "kind": sym.kind,
        "name": sym.name,
        "start": sym.start,
        "end": sym.end,
        "children": [symbol_to_dict(child) for child in sym.children],
    }
