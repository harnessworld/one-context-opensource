"""Public-workspace defaults and generated overview privacy regressions."""
from __future__ import annotations

from pathlib import Path

import pytest

from one_context.project_artifacts import build_agents_md
from one_context.smart_commit import RepoState, SmartCommitConfig, _scan_repo


@pytest.mark.parametrize("source", [
    "knowledge/01_Compiled/one-context/project-overview.md",
    "knowledge/references/project-overview.md",
])
def test_private_overview_is_not_inlined(tmp_path: Path, source: str) -> None:
    overview = tmp_path / source
    overview.parent.mkdir(parents=True)
    overview.write_text(
        "---\nvisibility: private\n---\n\n## Private overview\n"
        "PRIVATE_OVERVIEW_SENTINEL\n", encoding="utf-8",
    )
    assert "PRIVATE_OVERVIEW_SENTINEL" not in build_agents_md(tmp_path)


def test_public_overview_is_inlined(tmp_path: Path) -> None:
    overview = tmp_path / "knowledge/01_Compiled/one-context/project-overview.md"
    overview.parent.mkdir(parents=True)
    overview.write_text("## Overview\nPUBLIC_OVERVIEW_SENTINEL\n", encoding="utf-8")
    assert "PUBLIC_OVERVIEW_SENTINEL" in build_agents_md(tmp_path)


def test_detached_workspace_defaults_to_main(tmp_path: Path) -> None:
    from tests.test_smart_commit import _init_repo, _commit_all, _git

    _init_repo(tmp_path, "development")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _commit_all(tmp_path)
    _git(tmp_path, "checkout", "--detach")
    state = _scan_repo(RepoState("main", tmp_path, ".", True), SmartCommitConfig())
    assert state.branch == ""
    assert state.target_branch == "main"


def test_attached_workspace_keeps_its_branch(tmp_path: Path) -> None:
    from tests.test_smart_commit import _init_repo

    _init_repo(tmp_path, "development")
    state = _scan_repo(RepoState("main", tmp_path, ".", True), SmartCommitConfig())
    assert state.target_branch == "development"
