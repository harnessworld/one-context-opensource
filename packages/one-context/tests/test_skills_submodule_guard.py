"""Tests for the skills-submodule-not-initialized guard.

When ``.gitmodules`` declares ``skills`` as a submodule but the working
tree's ``skills/`` directory holds no ``SKILL.md`` files (the classic
signature of a fresh clone without ``git submodule update --init``),
``discover_skills`` must raise ``SkillsSubmoduleNotInitialized`` instead
of silently returning an empty list. An empty list would otherwise let
``onecxt adapt`` emit empty routing tables / skill registrations that
silently drift into commits.

Backwards-compat invariants preserved by these tests:

* ``skills/`` absent entirely → empty list, no raise (legacy behaviour).
* ``skills/`` present with real SKILL.md files → list of parsed metas.
* ``.gitmodules`` absent + empty ``skills/`` → empty list, no raise
  (still legal for repos that legitimately ship no skills).
* ``.gitmodules`` declares skills + ``skills/`` holds only ``.git`` → raise.
* ``.gitmodules`` declares skills + ``skills/`` is missing entirely → raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from one_context.errors import SkillsSubmoduleNotInitialized
from one_context.skills import discover_skills


SKILL_MD_TEMPLATE = """\
---
name: {name}
description: A test skill.
triggers:
  - {name}
---

# {name}

Body text.
"""


def _write_skill(skills_dir: Path, name: str) -> None:
    sub = skills_dir / name
    sub.mkdir(parents=True)
    (sub / "SKILL.md").write_text(
        SKILL_MD_TEMPLATE.format(name=name), encoding="utf-8"
    )


def _write_gitmodules(root: Path, entries: list[tuple[str, str, str]] | None = None) -> None:
    """Write a .gitmodules. If entries is None, write one declaring skills."""
    if entries is None:
        entries = [("skills", "skills", "https://example.test/oc/skills.git")]
    lines: list[str] = []
    for name, path, url in entries:
        lines.append(f'[submodule "{name}"]')
        lines.append(f"\tpath = {path}")
        lines.append(f"\turl = {url}")
        lines.append("")
    (root / ".gitmodules").write_text("\n".join(lines), encoding="utf-8")


def test_no_skills_dir_returns_empty(tmp_path: Path) -> None:
    """No skills/ at all — legacy contract still holds."""
    assert discover_skills(tmp_path) == []


def test_skills_dir_with_real_skill_returns_list(tmp_path: Path) -> None:
    """Real skills present — guard does not fire."""
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "alpha")
    _write_skill(skills, "beta")
    (tmp_path / "knowledge").mkdir()  # also no .gitmodules
    metas = discover_skills(tmp_path)
    assert {m.dir_name for m in metas} == {"alpha", "beta"}


def test_empty_skills_without_gitmodules_returns_empty(tmp_path: Path) -> None:
    """Empty skills/ + no .gitmodules — legal, no raise."""
    (tmp_path / "skills").mkdir()
    assert discover_skills(tmp_path) == []


def test_gitmodules_no_skills_submodule_returns_empty(tmp_path: Path) -> None:
    """If .gitmodules declares only other submodules, no guard fires."""
    (tmp_path / "skills").mkdir()
    _write_gitmodules(
        tmp_path,
        entries=[("knowledge", "knowledge", "https://example.test/k.git")],
    )
    assert discover_skills(tmp_path) == []


def test_submodule_declared_but_dir_missing_raises(tmp_path: Path) -> None:
    """Declared in .gitmodules, but skills/ directory absent — raise."""
    _write_gitmodules(tmp_path)
    with pytest.raises(SkillsSubmoduleNotInitialized) as excinfo:
        discover_skills(tmp_path)
    msg = str(excinfo.value)
    assert "git submodule update --init" in msg
    assert "skills" in msg


def test_submodule_declared_but_dir_empty_raises(tmp_path: Path) -> None:
    """Declared in .gitmodules, skills/ exists but empty — raise."""
    (tmp_path / "skills").mkdir()
    _write_gitmodules(tmp_path)
    with pytest.raises(SkillsSubmoduleNotInitialized):
        discover_skills(tmp_path)


def test_submodule_with_only_git_pointer_raises(tmp_path: Path) -> None:
    """skills/ contains only a ``.git`` file (the uninitialized pointer) — raise."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / ".git").write_text("gitdir: ../../.git/modules/skills", encoding="utf-8")
    _write_gitmodules(tmp_path)
    with pytest.raises(SkillsSubmoduleNotInitialized):
        discover_skills(tmp_path)


def test_submodule_with_real_skills_does_not_raise(tmp_path: Path) -> None:
    """Golden path: submodule declared AND populated — no raise."""
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "alpha")
    (skills / ".git").write_text("gitdir: ../../.git/modules/skills", encoding="utf-8")
    _write_gitmodules(tmp_path)
    metas = discover_skills(tmp_path)
    assert [m.dir_name for m in metas] == ["alpha"]


def test_gitmodules_declares_other_submodule_plus_empty_skills_no_raise(tmp_path: Path) -> None:
    """If .gitmodules declares *another* submodule (not skills) and skills/ is
    empty, do not raise — guard only fires for skills specifically."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "knowledge").mkdir()
    _write_gitmodules(
        tmp_path,
        entries=[("knowledge", "knowledge", "https://example.test/k.git")],
    )
    assert discover_skills(tmp_path) == []