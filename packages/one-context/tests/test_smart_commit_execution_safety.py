"""Local Git regressions for reviewed content and recoverable multi-repo publishing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from one_context import smart_commit as sc
from one_context.cli import build_parser
from one_context.cli._smart_commit import _cmd_smart_commit


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init(root: Path, branch: str = "main", *, bare: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", branch, *(["--bare"] if bare else []))
    if not bare:
        _identity(root)


def _identity(root: Path) -> None:
    _git(root, "config", "user.name", "Execution Test")
    _git(root, "config", "user.email", "execution@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    hooks = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "empty-hooks"
    hooks.mkdir(exist_ok=True)
    _git(root, "config", "core.hooksPath", str(hooks))


def _commit(root: Path, message: str = "fixture") -> str:
    _git(root, "add", "--all")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _repo(root: Path) -> None:
    _init(root, "development")
    (root / "README.md").write_text("initial\n", encoding="utf-8")
    (root / ".onecxt").mkdir()
    (root / ".onecxt" / "sensitive-words.yaml").write_text(
        "words:\n  - keyword: confidential_fixture\n",
        encoding="utf-8",
    )
    _commit(root)


def _workspace(base: Path) -> tuple[Path, Path, Path, Path]:
    child_remote = base / "child.git"
    main_remote = base / "main.git"
    _init(child_remote, bare=True)
    _init(main_remote, "development", bare=True)
    seed = base / "seed"
    _init(seed)
    (seed / "demo").mkdir()
    (seed / "demo" / "SKILL.md").write_text("initial\n", encoding="utf-8")
    _commit(seed)
    _git(seed, "remote", "add", "origin", str(child_remote))
    _git(seed, "push", "origin", "main")
    root = base / "workspace"
    _repo(root)
    _git(root, "-c", "protocol.file.allow=always", "submodule", "add", str(child_remote), "skills")
    child = root / "skills"
    _identity(child)
    _commit(root)
    _git(root, "remote", "add", "origin", str(main_remote))
    _git(root, "push", "origin", "development")
    return root, child, main_remote, child_remote


@pytest.fixture
def local_transport(monkeypatch):
    """Keep the real preflight/push/verification code; use only local bare remotes."""
    original = sc._to_ssh
    monkeypatch.setattr(
        sc, "_to_ssh", lambda url: url if Path(url).is_absolute() else original(url)
    )
    return sc.SmartCommitConfig(transport="ssh")


def test_same_status_content_change_cannot_bypass_sensitive_review(tmp_path: Path) -> None:
    root, child, _, _ = _workspace(tmp_path)
    document = child / "demo" / "SKILL.md"
    document.write_text("reviewed\n", encoding="utf-8")
    plan = sc.build_plan(root, mode="full", config=sc.SmartCommitConfig(), push=False)
    assert plan.safe
    before = _git(child, "rev-parse", "HEAD")
    status = _git(child, "status", "--porcelain=v2")
    document.write_text("confidential_fixture\n", encoding="utf-8")
    assert _git(child, "status", "--porcelain=v2") == status
    with pytest.raises(sc.SmartCommitError, match="changed after planning"):
        sc.execute_plan(plan, push=False)
    assert _git(child, "rev-parse", "HEAD") == before


def test_main_cannot_silently_include_a_new_file_after_child_commit(
    tmp_path: Path, monkeypatch
) -> None:
    root, child, _, _ = _workspace(tmp_path)
    (child / "demo" / "SKILL.md").write_text("reviewed\n", encoding="utf-8")
    plan = sc.build_plan(root, mode="full", config=sc.SmartCommitConfig(), push=False)
    before = _git(root, "rev-parse", "HEAD")
    original = sc._commit_repo

    def commit_with_concurrent_edit(repo, config, **kwargs):
        result = original(repo, config, **kwargs)
        if not repo.is_main:
            (root / "docs").mkdir()
            (root / "docs" / "unreviewed.md").write_text("new concurrent work\n", encoding="utf-8")
        return result

    monkeypatch.setattr(sc, "_commit_repo", commit_with_concurrent_edit)
    with pytest.raises(sc.SmartCommitError, match="changed after planning"):
        sc.execute_plan(plan, push=False)
    assert _git(root, "rev-parse", "HEAD") == before
    assert (root / "docs" / "unreviewed.md").is_file()


@pytest.mark.parametrize("mutation", ["content", "index", "head", "branch"])
def test_main_snapshot_binds_reviewed_git_state(tmp_path: Path, mutation: str) -> None:
    _repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("confidential_fixture approved\n", encoding="utf-8")
    plan = sc.build_plan(
        tmp_path,
        mode="main",
        config=sc.SmartCommitConfig(),
        push=False,
        allow_sensitive=True,
    )
    assert plan.safe
    if mutation == "content":
        readme.write_text("confidential_fixture unreviewed\n", encoding="utf-8")
    elif mutation == "index":
        _git(tmp_path, "add", "README.md")
    elif mutation == "head":
        _git(tmp_path, "commit", "--allow-empty", "-m", "concurrent commit")
    else:
        _git(tmp_path, "switch", "-c", "another-branch")
    head = _git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(sc.SmartCommitError, match="changed after planning"):
        sc.execute_plan(plan, push=False)
    assert _git(tmp_path, "rev-parse", "HEAD") == head


def test_staged_content_is_checked_after_git_add(tmp_path: Path, monkeypatch) -> None:
    _repo(tmp_path)
    (tmp_path / "README.md").write_text("reviewed\n", encoding="utf-8")
    plan = sc.build_plan(tmp_path, mode="main", config=sc.SmartCommitConfig(), push=False)
    before = _git(tmp_path, "rev-parse", "HEAD")
    original = sc._stage_paths

    def stage_with_concurrent_edit(repo, paths, config):
        original(repo, paths, config)
        (tmp_path / "README.md").write_text("unreviewed\n", encoding="utf-8")
        _git(tmp_path, "add", "README.md")

    monkeypatch.setattr(sc, "_stage_paths", stage_with_concurrent_edit)
    with pytest.raises(sc.SmartCommitError, match="staged content changed"):
        sc.execute_plan(plan, push=False)
    assert _git(tmp_path, "rev-parse", "HEAD") == before


def test_crlf_conversion_preserves_valid_commits(tmp_path: Path) -> None:
    _repo(tmp_path)
    _git(tmp_path, "config", "core.autocrlf", "true")
    (tmp_path / "README.md").write_bytes(b"reviewed\r\n")
    plan = sc.build_plan(tmp_path, mode="main", config=sc.SmartCommitConfig(), push=False)
    assert plan.safe
    sc.execute_plan(plan, push=False)
    assert _git(tmp_path, "show", "HEAD:README.md") == "reviewed"


def test_symlink_can_be_committed_without_following_target(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "docs").mkdir()
    link = tmp_path / "docs" / "guide.md"
    try:
        link.symlink_to("../README.md")
    except OSError:
        pytest.skip("symlinks require OS privileges")
    plan = sc.build_plan(tmp_path, mode="main", config=sc.SmartCommitConfig(), push=False)
    assert plan.safe
    sc.execute_plan(plan, push=False)
    assert _git(tmp_path, "show", "HEAD:docs/guide.md") == "../README.md"


def test_clean_pending_commit_is_pushed_and_sets_upstream(tmp_path: Path, local_transport) -> None:
    root, _, main_remote, _ = _workspace(tmp_path)
    (root / "README.md").write_text("pending\n", encoding="utf-8")
    pending = _commit(root)
    plan = sc.build_plan(root, mode="full", config=local_transport, push=True)
    assert plan.safe, plan.blockers
    assert not plan.changed_repos
    assert any(repo.push_required for repo in plan.repos)
    assert _git(main_remote, "rev-parse", "development") != pending  # Planning never pushes.
    result = sc.execute_plan(plan, push=True)
    assert _git(main_remote, "rev-parse", "development") == pending
    assert _git(root, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/development"
    assert "pushed+verified" in sc.format_result(result)


def test_commit_only_does_not_probe_or_publish_pending_commits(tmp_path: Path, monkeypatch) -> None:
    root, _, main_remote, _ = _workspace(tmp_path)
    old = _git(main_remote, "rev-parse", "development")
    (root / "README.md").write_text("pending\n", encoding="utf-8")
    _commit(root)

    def unexpected(*args, **kwargs):
        pytest.fail("commit-only must not contact remotes")

    monkeypatch.setattr(sc, "select_push_plan", unexpected)
    plan = sc.build_plan(root, mode="full", config=sc.SmartCommitConfig(), push=False)
    sc.execute_plan(plan, push=False)
    assert _git(main_remote, "rev-parse", "development") == old


def test_push_is_a_noop_for_clean_repo_without_origin(tmp_path: Path) -> None:
    _repo(tmp_path)
    plan = sc.build_plan(
        tmp_path,
        mode="main",
        config=sc.SmartCommitConfig(),
        push=True,
    )
    assert plan.safe
    assert not plan.active_repos
    sc.execute_plan(plan, push=True)


def test_pending_history_reviews_sensitive_content_even_if_later_reverted(
    tmp_path: Path, local_transport
) -> None:
    root, _, _, _ = _workspace(tmp_path)
    readme = root / "README.md"
    readme.write_text("confidential_fixture\n", encoding="utf-8")
    _commit(root)
    readme.write_text("initial\n", encoding="utf-8")
    _commit(root)
    plan = sc.build_plan(root, mode="full", config=local_transport, push=True)
    assert not plan.safe
    assert any("--allow-sensitive" in blocker for blocker in plan.blockers)
    approved = sc.build_plan(
        root, mode="full", config=local_transport, push=True, allow_sensitive=True
    )
    assert approved.safe, approved.blockers
    sc.execute_plan(approved, push=True)


@pytest.mark.parametrize("operation", ["delete", "rename", "excluded"])
def test_pending_history_preserves_other_safety_gates(
    tmp_path: Path, local_transport, operation: str
) -> None:
    root, _, _, _ = _workspace(tmp_path)
    if operation == "delete":
        (root / "README.md").unlink()
    elif operation == "rename":
        (root / "docs").mkdir()
        _git(root, "mv", "README.md", "docs/guide.md")
    else:
        (root / ".env").write_text("DEMO=fixture\n", encoding="utf-8")
    _commit(root)
    plan = sc.build_plan(root, mode="full", config=local_transport, push=True)
    assert not plan.safe
    expected = f"--allow-{operation}" if operation != "excluded" else "excluded path is already"
    assert any(expected in blocker for blocker in plan.blockers)


def test_child_push_failure_blocks_main_and_clean_retry_publishes_both(
    tmp_path: Path,
    local_transport,
    monkeypatch,
) -> None:
    root, child, main_remote, child_remote = _workspace(tmp_path)
    old_main = _git(main_remote, "rev-parse", "development")
    (child / "demo" / "SKILL.md").write_text("reviewed\n", encoding="utf-8")
    plan = sc.build_plan(root, mode="full", config=local_transport, push=True)
    assert plan.safe, plan.blockers
    original = sc._run_prefixed

    def reject_child(repo, command, *args, **kwargs):
        if repo == child and "push" in command and "--dry-run" not in command:
            return subprocess.CompletedProcess(command, 1, "", "synthetic server rejection")
        return original(repo, command, *args, **kwargs)

    monkeypatch.setattr(sc, "_run_prefixed", reject_child)
    with pytest.raises(sc.SmartCommitError, match="push failed"):
        sc.execute_plan(plan, push=True)
    assert _git(main_remote, "rev-parse", "development") == old_main
    assert not _git(root, "status", "--porcelain")
    assert not _git(child, "status", "--porcelain")
    monkeypatch.setattr(sc, "_run_prefixed", original)
    retry = sc.build_plan(root, mode="full", config=local_transport, push=True)
    assert retry.safe, retry.blockers
    assert not retry.changed_repos
    sc.execute_plan(retry, push=True)
    assert _git(main_remote, "rev-parse", "development") == _git(root, "rev-parse", "HEAD")
    assert _git(child_remote, "rev-parse", "main") == _git(child, "rev-parse", "HEAD")


def test_detached_child_pending_commit_is_published_before_main(
    tmp_path: Path, local_transport
) -> None:
    root, child, main_remote, child_remote = _workspace(tmp_path)
    _git(child, "checkout", "--detach")
    (child / "demo" / "SKILL.md").write_text("pending detached commit\n", encoding="utf-8")
    pending = _commit(child)
    _commit(root)
    plan = sc.build_plan(root, mode="full", config=local_transport, push=True)
    assert plan.safe, plan.blockers
    sc.execute_plan(plan, push=True)
    assert _git(child_remote, "rev-parse", "main") == pending
    assert _git(main_remote, "ls-tree", "development", "skills").split()[2] == pending
    assert _git(child, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"


def test_main_only_refuses_to_publish_an_unpublished_child_pointer(
    tmp_path: Path, local_transport
) -> None:
    root, child, main_remote, _ = _workspace(tmp_path)
    before = _git(main_remote, "rev-parse", "development")
    (child / "demo" / "SKILL.md").write_text("pending\n", encoding="utf-8")
    _commit(child)
    plan = sc.build_plan(root, mode="main", config=local_transport, push=True)
    assert plan.safe, plan.blockers
    with pytest.raises(sc.SmartCommitError, match="not verified on its remote"):
        sc.execute_plan(plan, push=True)
    assert _git(main_remote, "rev-parse", "development") == before


def test_reviewed_plan_id_rejects_same_status_content_change(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _repo(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("reviewed\n", encoding="utf-8")
    reviewed = sc.build_plan(
        tmp_path,
        mode="main",
        config=sc.SmartCommitConfig(),
        push=False,
    )
    before = _git(tmp_path, "rev-parse", "HEAD")
    status = _git(tmp_path, "status", "--porcelain=v2")
    readme.write_text("changed after review\n", encoding="utf-8")
    assert _git(tmp_path, "status", "--porcelain=v2") == status
    args = build_parser().parse_args(
        [
            "smart-commit",
            "--mode",
            "main",
            "--yes",
            "--plan-id",
            reviewed.plan_id,
        ]
    )
    assert _cmd_smart_commit(tmp_path, args) == 2
    assert "does not match current plan" in capsys.readouterr().err
    assert _git(tmp_path, "rev-parse", "HEAD") == before


def test_reviewed_plan_id_executes_unchanged_plan(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "README.md").write_text("reviewed\n", encoding="utf-8")
    reviewed = sc.build_plan(
        tmp_path,
        mode="main",
        config=sc.SmartCommitConfig(),
        push=False,
    )
    before = _git(tmp_path, "rev-parse", "HEAD")
    args = build_parser().parse_args(
        [
            "smart-commit",
            "--mode",
            "main",
            "--yes",
            "--plan-id",
            reviewed.plan_id,
        ]
    )
    assert _cmd_smart_commit(tmp_path, args) == 0
    assert _git(tmp_path, "rev-parse", "HEAD") != before
