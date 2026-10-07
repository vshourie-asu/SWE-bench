#!/usr/bin/env python3
"""Repo_Cloner: local git clones of task repositories, checked out at a base commit.

Each task repository is cloned once into a shared cache keyed by ``<org>__<repo>``
and reused across tasks (the cache key is the repo, not the instance). Checkouts
switch the shared working tree to the requested ``base_commit``.

Design notes (see .kiro/specs/swebench-change-viz/design.md -> Repo_Cloner):
- Full clone, not shallow: a ``--depth 1`` clone only has the tip commit, so an
  arbitrary historical ``base_commit`` SHA would not be checkoutable. A full clone
  keeps any SHA reachable.
- ``checkout`` falls back to ``git fetch origin <sha>`` and retries when the commit
  is not present locally (also covers a pre-existing shallow clone).
- Windows path safety: all paths are :class:`pathlib.Path` and every git call uses
  list-form ``subprocess.run`` (no shell), so spaces and backslashes in
  ``C:\\Users\\...`` paths need no quoting.
- No Docker: all repository operations use the local ``git`` executable, which must
  be on ``PATH`` (Git for Windows / the ``trace-sims`` env provides it).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def cache_path(repo: str, cache_root: Path) -> Path:
    """Map an ``org/name`` repo to its cache directory.

    ``django/django`` -> ``<cache_root>/django__django``. The mapping is
    deterministic (equal repos share a cache dir, enabling reuse) and injective
    (distinct repos never collide), because ``/`` is the only separator replaced.
    """
    return cache_root / repo.replace("/", "__")


def ensure_clone(repo: str, cache_root: Path) -> Path:
    """Return the local clone dir for ``repo``, creating a full clone if needed.

    Reuses an existing clone when its ``.git`` directory is present; otherwise runs
    a full ``git clone https://github.com/<repo>.git <dest>`` so any ``base_commit``
    SHA remains checkoutable.
    """
    dest = cache_path(repo, cache_root)
    if (dest / ".git").is_dir():
        return dest  # reuse existing clone (Req 2.3)
    url = f"https://github.com/{repo}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", url, str(dest)])  # full clone: need arbitrary-SHA history
    return dest


def checkout(repo_dir: Path, base_commit: str) -> None:
    """Force-check-out ``base_commit`` in ``repo_dir``'s working tree.

    ``--force`` discards any prior checkout's tree state cleanly. If the commit is
    not present locally, fetch just that object from ``origin`` and retry (Req 2.2).
    """
    try:
        _git(["checkout", "--force", base_commit], cwd=repo_dir)
    except subprocess.CalledProcessError:
        # Commit not present locally: fetch just that object, then retry.
        _git(["fetch", "origin", base_commit], cwd=repo_dir)
        _git(["checkout", "--force", base_commit], cwd=repo_dir)


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` with no shell, raising on non-zero exit.

    stdout/stderr are captured as text so a failure surfaces a readable message
    (via the raised :class:`subprocess.CalledProcessError`) rather than a wall of
    git output on the console.
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
