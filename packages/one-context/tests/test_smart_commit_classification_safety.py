"""Behavior regressions for Smart Commit exclusions and mixed SKIP plans."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from one_context.smart_commit import (
    Change,
    RepoState,
    SmartCommitConfig,
    build_plan,
    classify_change,
    execute_plan,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Safety Test")
    _git(root, "config", "user.email", "safety@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    hooks = root / ".git" / "empty-hooks"
    hooks.mkdir()
    _git(root, "config", "core.hooksPath", str(hooks))
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")


@pytest.mark.parametrize(
    ("repo_name", "path"),
    [
        ("knowledge", "scratch/.env"),
        ("knowledge", "node_modules/demo/index.js"),
        ("knowledge", ".agents/demo/SKILL.md"),
        ("knowledge", "tmp/draft.md"),
        ("skills", "demo/assets/.env"),
        ("skills", "demo/assets/node_modules/demo/index.js"),
        ("skills", "demo/assets/tmp/draft.md"),
        ("main", "docs/images/.env"),
        ("main", "docs/images/node_modules/demo/index.js"),
    ],
)
@pytest.mark.parametrize("tracked", [False, True])
def test_assets_never_override_non_media_exclusions(
    tmp_path: Path,
    repo_name: str,
    path: str,
    tracked: bool,
) -> None:
    repo = RepoState(
        repo_name,
        tmp_path,
        "." if repo_name == "main" else repo_name,
        repo_name == "main",
    )
    change = Change(path, "M" if tracked else "?", ".M" if tracked else "??", tracked)
    assert classify_change(repo, change, set()).mark == "SKIP"


@pytest.mark.parametrize(
    ("repo_name", "path"),
    [
        ("knowledge", "attachments/diagram.png"),
        ("knowledge", "attachments/lecture.mp3"),
        ("skills", "demo/assets/logo.png"),
        ("skills", "tools/design/demo/assets/reference.jpg"),
        ("main", "docs/images/diagram.png"),
    ],
)
def test_legitimate_assets_remain_committable(
    tmp_path: Path,
    repo_name: str,
    path: str,
) -> None:
    repo = RepoState(
        repo_name,
        tmp_path,
        "." if repo_name == "main" else repo_name,
        repo_name == "main",
    )
    assert classify_change(repo, Change(path, "?", "??", False), set()).mark == "OK"


@pytest.mark.parametrize("pre_staged", [False, True])
def test_mixed_skip_and_source_commit_excludes_env(
    tmp_path: Path,
    pre_staged: bool,
) -> None:
    _init(tmp_path)
    (tmp_path / "README.md").write_text("updated\n", encoding="utf-8")
    secret = tmp_path / "docs" / "images" / ".env"
    secret.parent.mkdir(parents=True)
    secret.write_text("DEMO=synthetic-fixture\n", encoding="utf-8")
    if pre_staged:
        _git(tmp_path, "add", "docs/images/.env")
    plan = build_plan(tmp_path, mode="main", config=SmartCommitConfig(), push=False)
    assert plan.safe, plan.blockers
    execute_plan(plan, push=False)
    committed = _git(tmp_path, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    assert committed == "README.md"
    assert secret.read_text(encoding="utf-8") == "DEMO=synthetic-fixture\n"
    assert not _git(tmp_path, "diff", "--cached", "--name-only")


def test_skip_only_plan_preserves_files_without_empty_commit(tmp_path: Path) -> None:
    _init(tmp_path)
    before = _git(tmp_path, "rev-parse", "HEAD")
    scratch = tmp_path / "tmp" / "scratch.txt"
    scratch.parent.mkdir()
    scratch.write_text("keep me\n", encoding="utf-8")
    plan = build_plan(tmp_path, mode="main", config=SmartCommitConfig(), push=False)
    assert plan.safe, plan.blockers
    execute_plan(plan, push=False)
    assert _git(tmp_path, "rev-parse", "HEAD") == before
    assert scratch.read_text(encoding="utf-8") == "keep me\n"


def test_skip_does_not_weaken_deletion_gate(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "README.md").unlink()
    (tmp_path / ".env").write_text("DEMO=fixture\n", encoding="utf-8")
    plan = build_plan(tmp_path, mode="main", config=SmartCommitConfig(), push=False)
    assert not plan.safe
    assert any("--allow-delete" in item for item in plan.blockers)
