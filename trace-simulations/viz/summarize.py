#!/usr/bin/env python3
"""Summary_Generator: per-file natural-language summaries of changes, with fallback.

Implements Requirement 4 of the SWE-bench change-visualization feature. For every
changed file node in a ``viz_data.json`` document (any file carrying a non-empty
``annotations`` object), this module requests one concise sentence describing the
change from the OpenAI-compatible CreateAI ``LLM_Endpoint`` and, on any failure,
records a deterministic templated fallback instead so processing always covers
every changed file.

Endpoint convention (see trace-simulations/README.md)
-----------------------------------------------------
The endpoint is OpenAI-compatible: ``POST {OPENAI_API_BASE}/chat/completions`` with
an ``Authorization: Bearer <token>`` header, where ``OPENAI_API_BASE`` already ends
in ``/v1`` (nothing is appended beyond ``/chat/completions``). The model name uses
litellm's ``openai/`` double-prefix convention, defaulting to
``openai/openai/gpt5_6_luna`` (first ``openai/`` selects the OpenAI-style client,
the second is CreateAI's provider path).

Secret hygiene (Requirements 4.5, 6.5)
--------------------------------------
Credentials are read from the repo-root ``.env`` into locals and used only to build
the request header. They are never logged and never written into ``viz_data.json``,
``manifest.json``, or any other emitted artifact. Fallback messages reference a
failure by its exception *type*, never by any credential value. The ``.env`` file is
already gitignored.

The module uses only the Python standard library (``urllib.request`` for the HTTP
call) so it adds no dependency beyond what the rest of the pipeline already needs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

__all__ = [
    "DEFAULT_MODEL",
    "load_credentials",
    "summarize_file",
    "fallback_summary",
    "summarize_all",
    "changed_file_nodes",
]

# CreateAI model name for the direct OpenAI-compatible gateway call. This module
# POSTs to {OPENAI_API_BASE}/chat/completions via urllib (NOT through litellm), so
# the model name is the gateway's own provider-path form `openai/gpt5_6_luna`. The
# litellm `openai/` double-prefix (`openai/openai/gpt5_6_luna`) is rejected by the
# gateway with HTTP 500 ("openai is not supported as query model by openai"), so it
# must not be used here.
DEFAULT_MODEL = "openai/gpt5_6_luna"

# Short system instruction that pins the response to a single sentence (Req 4.1).
_SYSTEM_PROMPT = "Summarize this code change in one sentence."

# Keep responses to a single sentence and bound latency / cost.
_MAX_TOKENS = 80

# Network timeout (seconds) for the endpoint call. A hung endpoint falls back.
_TIMEOUT = 30


def load_credentials(env_path: Path) -> tuple[str | None, str | None]:
    """Parse ``OPENAI_API_BASE`` and ``OPENAI_API_KEY`` from the repo-root ``.env``.

    A deliberately small ``KEY=VALUE`` parser: it tolerates a leading ``export``,
    surrounding whitespace, blank lines, ``#`` comments, and single/double quotes
    around the value. Returns ``(base, key)`` with ``None`` substituted for any
    value that is missing or empty. The credential *values* are never logged.

    A missing or unreadable ``.env`` yields ``(None, None)`` so the caller falls
    back cleanly rather than raising.
    """
    env_path = Path(env_path)
    values: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None, None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        # Strip a single matched pair of surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name in ("OPENAI_API_BASE", "OPENAI_API_KEY"):
            values[name] = value

    base = values.get("OPENAI_API_BASE") or None
    key = values.get("OPENAI_API_KEY") or None
    return base, key


def summarize_file(path: str, diff_text: str, base: str, key: str, model: str) -> str:
    """Return a one-sentence summary of ``path``'s change from the LLM_Endpoint.

    Issues a stdlib ``urllib.request`` POST to ``{base}/chat/completions`` with an
    ``Authorization: Bearer {key}`` header and a short chat-completion body (system
    instruction + the file path and its diff, small ``max_tokens``). Raises on any
    transport, credential, HTTP, or malformed-response error so the caller can fall
    back; the token appears only in the request header, never in a raised message.
    """
    if not base or not key:
        raise RuntimeError("credentials unavailable")

    url = f"{base.rstrip('/')}/chat/completions"
    user_content = f"File: {path}\n\nUnified diff:\n{diff_text}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": _MAX_TOKENS,
        "temperature": 0,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        body = response.read().decode("utf-8")

    parsed = json.loads(body)
    choices = parsed.get("choices")
    if not choices:
        raise RuntimeError("endpoint response contained no choices")
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        raise RuntimeError("endpoint response contained an empty message")
    # Collapse to a single line so the stored summary is one clean sentence.
    return " ".join(content.split())


def fallback_summary(path: str, added: int, removed: int) -> str:
    """Return a deterministic templated summary from path + add/remove counts.

    Used whenever the LLM_Endpoint cannot be reached or credentials are missing so
    that every changed file still carries a non-empty summary (Req 4.4).
    """
    return f"{path}: {added} line(s) added, {removed} line(s) removed."


def changed_file_nodes(doc: dict):
    """Yield each file node in ``doc`` that carries at least one layer annotation.

    Walks the nested directory/file ``tree`` iteratively. A file node is "changed"
    when its ``annotations`` object is non-empty (one or more of model/gold/test).
    """
    tree = doc.get("tree")
    if not tree:
        return
    stack = [tree]
    while stack:
        node = stack.pop()
        node_type = node.get("type")
        if node_type == "dir":
            stack.extend(node.get("children") or [])
        elif node_type == "file":
            if node.get("annotations"):
                yield node


def _combined_diff_text(node: dict) -> str:
    """Build a combined unified-diff-ish text across every layer of a file node.

    Concatenates each layer's hunk headers and classified lines so the endpoint
    sees the full change for the file regardless of which patch(es) touched it.
    """
    annotations = node.get("annotations") or {}
    sections: list[str] = []
    for layer in sorted(annotations):
        layer_ann = annotations[layer] or {}
        hunks = layer_ann.get("hunks") or []
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


def _total_counts(node: dict) -> tuple[int, int]:
    """Sum ``added``/``removed`` across every layer annotation of a file node."""
    added = 0
    removed = 0
    for layer_ann in (node.get("annotations") or {}).values():
        if not layer_ann:
            continue
        added += int(layer_ann.get("added", 0) or 0)
        removed += int(layer_ann.get("removed", 0) or 0)
    return added, removed


def summarize_all(doc: dict, env_path: Path, model: str = DEFAULT_MODEL) -> None:
    """Attach a ``summary`` to every changed file node in ``doc`` (in place).

    Reads credentials from ``env_path`` once, then for each changed file tries the
    LLM_Endpoint and, on *any* failure (missing credentials, unreachable endpoint,
    timeout, HTTP error, malformed response), records a deterministic fallback and
    continues to the next file (Req 4.4). Sets the top-level ``generated_with_llm``
    boolean to ``True`` only when every changed file was summarized by the endpoint
    with no fallback used, so the paper's methods section can state it accurately.

    On a per-file failure a line ``  [fallback] <path>: <ExceptionType>`` is printed;
    the credential token is never printed or stored.
    """
    base, key = load_credentials(env_path)

    nodes = list(changed_file_nodes(doc))
    used_llm_everywhere = True
    any_changed = False

    for node in nodes:
        any_changed = True
        path = node.get("path", "")
        combined = _combined_diff_text(node)
        try:
            node["summary"] = summarize_file(path, combined, base, key, model)
        except Exception as exc:  # endpoint down, auth error, timeout, bad response...
            added, removed = _total_counts(node)
            node["summary"] = fallback_summary(path, added, removed)
            used_llm_everywhere = False
            # Report by exception type only -- never the token or endpoint response.
            print(f"  [fallback] {path}: {type(exc).__name__}")

    # True only if there was at least one changed file and all used the endpoint.
    doc["generated_with_llm"] = bool(any_changed and used_llm_everywhere)
