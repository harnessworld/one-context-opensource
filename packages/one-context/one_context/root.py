from __future__ import annotations

import os
from pathlib import Path

from one_context.errors import ManifestError


def find_root(start: Path | None = None) -> Path:
    """
    Return the one-context root directory.

    A root is a directory containing ``meta/`` with at least one of:
      * ``meta/repos.yaml``     — tracked repo registry
      * ``meta/agents.yaml``    — tracked agent registry

    Stripped or newly bootstrapped workspaces without ``meta/repos.yaml`` are
    still recognised via ``meta/agents.yaml`` and get an actionable hint from
    ``load_repos``.

    Order:
    1. ONECXT_ROOT environment variable (must satisfy the rule above)
    2. Walk parents from start (default: cwd) looking for a matching directory
    """
    raw = os.environ.get("ONECXT_ROOT", "").strip()
    if raw:
        root = Path(raw).expanduser().resolve()
        if not _looks_like_root(root):
            raise ManifestError(
                f"ONECXT_ROOT is set to {root!s} but neither meta/repos.yaml "
                f"nor meta/agents.yaml is present"
            )
        return root

    cur = (start or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if _looks_like_root(p):
            return p

    raise ManifestError(
        "Could not find a one-context root in the current directory or any "
        "parent. Set ONECXT_ROOT or run from inside the one-context workspace."
    )


def _looks_like_root(p: Path) -> bool:
    meta = p / "meta"
    return meta.is_dir() and (
        (meta / "repos.yaml").is_file() or (meta / "agents.yaml").is_file()
    )
