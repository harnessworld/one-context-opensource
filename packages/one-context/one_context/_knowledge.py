"""Shared knowledge-reference resolution and privacy filtering."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml


_PRIVATE_VISIBILITY_RE = re.compile(
    r"(?im)^\s*visibility\s*:\s*['\"]?private['\"]?\s*(?:#.*)?$"
)


def _is_private_markdown(path: Path) -> bool:
    """Return whether Markdown frontmatter marks *path* private."""
    if path.suffix.casefold() != ".md":
        return False
    try:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except (OSError, UnicodeDecodeError):
        return False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    try:
        closing = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return _PRIVATE_VISIBILITY_RE.search(text) is not None
    frontmatter_text = "\n".join(lines[1:closing])
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError:
        return _PRIVATE_VISIBILITY_RE.search(frontmatter_text) is not None
    if not isinstance(frontmatter, dict):
        return False
    visibility = frontmatter.get("visibility")
    return isinstance(visibility, str) and visibility.casefold() == "private"


def resolve_knowledge_entries(
    root: Path,
    raw_paths: Iterable[object],
) -> list[dict[str, Any]]:
    """Resolve knowledge paths, expanding directories and excluding private notes."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(rel_path: Path, target: Path, target_type: str) -> None:
        path = rel_path.as_posix()
        if path in seen:
            return
        seen.add(path)
        out.append(
            {
                "path": path,
                "absolute_path": str(target),
                "exists": target.exists(),
                "type": target_type,
            }
        )

    for item in raw_paths:
        if not isinstance(item, str) or not item.strip():
            continue
        rel = Path(item.strip())
        target = (root / rel).resolve()
        if target.is_file():
            if not _is_private_markdown(target):
                add(rel, target, "file")
            continue
        if target.is_dir():
            markdown_files = sorted(target.rglob("*.md"))
            if not markdown_files:
                add(rel, target, "directory")
                continue
            for child in markdown_files:
                if _is_private_markdown(child):
                    continue
                child_rel = rel / child.relative_to(target)
                add(child_rel, child.resolve(), "file")
            continue
        add(rel, target, "missing")

    return out
