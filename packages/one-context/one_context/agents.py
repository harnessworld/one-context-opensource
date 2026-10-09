"""Load and validate meta/agents.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from one_context._identifiers import is_portable_manifest_id
from one_context._knowledge import resolve_knowledge_entries
from one_context.errors import ManifestError


def is_safe_agent_id(agent_id: str) -> bool:
    """Return whether *agent_id* is one portable, traversal-safe component."""
    return is_portable_manifest_id(agent_id)


def load_agents(root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    Parse meta/agents.yaml if present.

    Returns ([], {}) when the file is missing.
    """
    path = root / "meta" / "agents.yaml"
    if not path.is_file():
        return [], {}

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ManifestError("agents.yaml root must be a mapping")

    raw_list = data.get("agents")
    if raw_list is None:
        return [], {}
    if not isinstance(raw_list, list):
        raise ManifestError("agents.yaml must contain an 'agents' list")

    agents: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}

    for i, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ManifestError(f"agents[{i}] must be a mapping")

        aid = item.get("id")
        if not aid or not isinstance(aid, str) or not aid.strip():
            raise ManifestError(f"agents[{i}] needs a non-empty string 'id'")
        aid = aid.strip()
        if not is_safe_agent_id(aid):
            raise ManifestError(
                f"agents[{i}] has invalid id {aid!r}; expected one portable "
                "lowercase path component using ASCII letters, digits, '.', "
                "'_' or '-'"
            )
        item["id"] = aid  # write back normalized id

        role = item.get("role")
        if not role or not isinstance(role, str) or not role.strip():
            raise ManifestError(f"agents[{i}] ({aid}): missing required 'role' field")

        lk = aid.casefold()
        if lk in by_id:
            raise ManifestError(f"Duplicate agent id (case-insensitive): {aid!r}")

        agents.append(item)
        by_id[lk] = item

    return agents, by_id


def resolve_agent_knowledge(root: Path, agent: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Resolve the ``knowledge`` paths for an agent.

    Returns the same structure as ``context._collect_knowledge_entries`` so
    adapter helpers can reuse the same inlining / @-reference logic.
    """
    return resolve_knowledge_entries(root, agent.get("knowledge") or [])
