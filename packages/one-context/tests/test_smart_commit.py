"""Tests for the cross-platform Smart Commit engine."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from unittest.mock import patch

from one_context.cli import build_parser
from one_context.smart_commit import (
    Change,
    PushPlan,
    RepoState,
    SmartCommitConfig,
    SmartCommitPlan,
    build_plan,
    classify_change,
    execute_plan,
    load_config,
    load_sensitive_rules,
    parse_porcelain_v2,
    scan_sensitive,
    select_push_plan,
    _detect_remote_ahead,
    _run_git,
    _run_prefixed,
)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(path),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-c", f"init.defaultBranch={branch}", "init", str(path)],
        check=True,
        capture_output=True,
    )
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "user.email", "test@example.com")


def _commit_all(path: Path, message: str = "initial") -> None:
    _git(path, "add", "--all")
    _git(path, "commit", "-m", message)


class TestParser:
    def test_smart_commit_options(self):
        args = build_parser().parse_args(
            [
                "smart-commit",
                "--mode",
                "main",
                "--yes",
                "--push",
                "--transport",
                "https",
                "--message",
                "main=feat(cli): test",
                "--ignore-github-token",
            ]
        )
        assert args.command == "smart-commit"
        assert args.mode == "main"
        assert args.yes is True
        assert args.push is True
        assert args.transport == "https"
        assert args.message == ["main=feat(cli): test"]
        assert args.ignore_github_token is True


class TestPorcelainParser:
    def test_spaces_unicode_and_rename(self):
        payload = b"".join(
            (
                b"? docs/a file.md\0",
                "? übersicht/hinweis.md\0".encode(),
                b"1 .M N... 100644 100644 100644 abc def tracked.txt\0",
                b"2 R. N... 100644 100644 100644 abc def R100 new name.txt\0old name.txt\0",
            )
        )
        changes = parse_porcelain_v2(payload)
        assert [change.path for change in changes] == [
            "docs/a file.md",
            "übersicht/hinweis.md",
            "tracked.txt",
            "new name.txt",
        ]
        assert changes[-1].original_path == "old name.txt"
        assert changes[-1].status == "R"


class TestLocalConfig:
    def test_loads_windows_friendly_machine_settings(self, tmp_path: Path):
        config_dir = tmp_path / ".onecxt"
        config_dir.mkdir()
        (config_dir / "local.yaml").write_text(
            textwrap.dedent(
                """\
                smart_commit:
                  transport: auto
                  transport_order: [https, ssh]
                  https_proxy: http://127.0.0.1:13659
                  timeout_seconds: 45
                  inherit_github_token: false
                  branches:
                    skills: main
                  coauthors: []
                """
            ),
            encoding="utf-8",
        )
        config = load_config(tmp_path)
        assert config.transport_order == ("https", "ssh")
        assert config.https_proxy == "http://127.0.0.1:13659"
        assert config.timeout_seconds == 45
        assert config.branches["skills"] == "main"
        assert config.coauthors == ()
        assert config.inherit_github_token is False


class TestSensitiveScan:
    def test_ascii_short_keyword_uses_word_boundaries(self, tmp_path: Path):
        _init_repo(tmp_path)
        (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
        _commit_all(tmp_path)
        config_dir = tmp_path / ".onecxt"
        config_dir.mkdir()
        (config_dir / "sensitive-words.yaml").write_text(
            "words:\n  - keyword: sampletoken\n    category: marker\n",
            encoding="utf-8",
        )
        candidate = tmp_path / "docs.txt"
        candidate.write_text(
            "sampletokenized is fine\nsampletoken must alert\n",
            encoding="utf-8",
        )
        repo = RepoState("main", tmp_path, ".", True)
        repo.changes = [Change("docs.txt", "?", "??", False)]
        hits = scan_sensitive(repo, load_sensitive_rules(tmp_path), 10)
        assert [(hit.line, hit.keyword) for hit in hits] == [(2, "sampletoken")]


class TestClassification:
    def test_skill_asset_is_canonical_even_when_binary(self, tmp_path: Path):
        repo = RepoState("skills", tmp_path, "skills", False)
        item = classify_change(
            repo,
            Change("demo/assets/logo.png", "?", "??", False),
            set(),
        )
        assert item.mark == "OK"

    def test_feature_cover_prompt_is_canonical(self, tmp_path: Path):
        repo = RepoState("features", tmp_path, "features", False)
        item = classify_change(
            repo,
            Change(
                "media/demo-video/production/cover-prompt.md",
                "?",
                "??",
                False,
            ),
            set(),
        )
        assert item.mark == "OK"

    def test_feature_remotion_lib_is_canonical(self, tmp_path: Path):
        repo = RepoState("features", tmp_path, "features", False)
        item = classify_change(
            repo,
            Change("media/demo-video/remotion/lib/timeline.js", "?", "??", False),
            set(),
        )
        assert item.mark == "OK"

    def test_feature_remotion_fonts_are_canonical(self, tmp_path: Path):
        repo = RepoState("features", tmp_path, "features", False)
        item = classify_change(
            repo,
            Change(
                "media/demo-video/remotion/public/fonts/demo/demo.woff2",
                "?",
                "??",
                False,
            ),
            set(),
        )
        assert item.mark == "OK"

    def test_feature_issue_checklist_is_canonical(self, tmp_path: Path):
        repo = RepoState("features", tmp_path, "features", False)
        item = classify_change(
            repo,
            Change("delivery/demo-feature/issue_checklist.md", "?", "??", False),
            set(),
        )
        assert item.mark == "OK"

    def test_feature_archive_is_canonical(self, tmp_path: Path):
        repo = RepoState("features", tmp_path, "features", False)
        item = classify_change(
            repo,
            Change("tutorial/demo/archive/remotion/src/Root.tsx", "?", "??", False),
            set(),
        )
        assert item.mark == "OK"

    def test_feature_episodes_skeleton_is_canonical(self, tmp_path: Path):
        repo = RepoState("features", tmp_path, "features", False)
        ok_paths = {
            "tutorial/demo/production/episodes/01-overview/content/00-structure.md",
            "tutorial/demo/production/episodes/01-overview/timing/plan.json",
            "tutorial/demo/production/episodes/01-overview/learning/feynman-review.json",
            "tutorial/demo/production/episodes/01-overview/slides/deck-plan.md",
            "tutorial/demo/production/episodes/02-deepdive/recorder/notes.md",
        }
        ask_paths = {
            "tutorial/demo/production/episodes/01-overview/README.md",
            "tutorial/demo/production/episodes/01-slides/deck-plan.md",
        }
        skip_paths = {
            "tutorial/demo/production/episodes/01-overview/stills/overview-k01.png",
            "tutorial/demo/production/episodes/01-overview/videos/overview-deck-preview.mp4",
        }
        for path in ok_paths:
            item = classify_change(repo, Change(path, "?", "??", False), set())
            assert item.mark == "OK", path
        for path in ask_paths:
            item = classify_change(repo, Change(path, "?", "??", False), set())
            assert item.mark == "ASK", path
        for path in skip_paths:
            item = classify_change(repo, Change(path, "?", "??", False), set())
            assert item.mark == "SKIP", path

    def test_feature_allowlist_keeps_unknown_and_generated_files_blocked(
        self,
        tmp_path: Path,
    ):
        repo = RepoState("features", tmp_path, "features", False)
        expectations = {
            "media/demo-video/remotion/public/metadata.json": "ASK",
            "media/demo-video/production/notes.md": "ASK",
            "media/demo-video/remotion/public/fonts/preview.png": "SKIP",
            "media/demo-video/production/cover.png": "SKIP",
        }

        for path, expected_mark in expectations.items():
            item = classify_change(repo, Change(path, "?", "??", False), set())
            assert item.mark == expected_mark, path

    def test_unmerged_change_is_always_a_blocker(self, tmp_path: Path):
        repo = RepoState("main", tmp_path, ".", True)
        change = Change("README.md", "U", "UU", True)
        repo.changes = [change]
        repo.classifications = [classify_change(repo, change, set())]
        plan = SmartCommitPlan(tmp_path, "main", [repo], SmartCommitConfig())
        assert any("unresolved merge conflict" in blocker for blocker in plan.blockers)


class TestPushPreflight:
    def test_ssh_command_is_an_argument_array_and_sets_upstream(self, tmp_path: Path):
        repo = RepoState(
            "main",
            tmp_path,
            ".",
            True,
            changes=[Change("README.md", "M", ".M", True)],
            branch="development",
            target_branch="development",
            remote_url="git@github.com:owner/repo.git",
        )
        config = SmartCommitConfig(transport="ssh")
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch("one_context.smart_commit._has_upstream", return_value=False), patch(
            "one_context.smart_commit._run_prefixed", return_value=completed
        ) as run:
            plan = select_push_plan(repo, config)
        assert plan is not None
        command = run.call_args.args[1]
        assert command[0] == "git"
        assert "core.sshCommand=ssh -o ProxyCommand=none -o BatchMode=yes -o ConnectTimeout=15" in command
        assert "--dry-run" in command
        assert "--set-upstream" in command
        assert command[-1] == "HEAD:refs/heads/development"

    def test_https_uses_git_config_not_shell_environment(self, tmp_path: Path):
        repo = RepoState(
            "skills",
            tmp_path,
            "skills",
            False,
            changes=[Change("smart-commit/SKILL.md", "M", ".M", True)],
            branch="main",
            target_branch="main",
            remote_url="git@github.com:owner/skills.git",
        )
        config = SmartCommitConfig(
            transport="https",
            https_proxy="http://127.0.0.1:13659",
            inherit_github_token=False,
        )
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch("one_context.smart_commit.proxy_reachable", return_value=(True, "")), patch(
            "one_context.smart_commit._has_upstream", return_value=True
        ), patch("one_context.smart_commit._run_prefixed", return_value=completed) as run:
            plan = select_push_plan(repo, config)
        assert plan is not None
        command = run.call_args.args[1]
        assert "http.proxy=http://127.0.0.1:13659" in command
        assert (
            "url.https://github.com/owner/skills.git.insteadOf="
            "git@github.com:owner/skills.git"
        ) in command
        assert all("HTTPS_PROXY=" not in item for item in command)
        assert plan.ignore_github_token is True

    def test_can_drop_invalid_environment_tokens_without_shell(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "invalid")
        monkeypatch.setenv("GH_TOKEN", "invalid")
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch("one_context.smart_commit.subprocess.run", return_value=completed) as run:
            _run_prefixed(
                tmp_path,
                ["git", "status"],
                10,
                ignore_github_token=True,
            )
        env = run.call_args.kwargs["env"]
        assert "GITHUB_TOKEN" not in env
        assert "GH_TOKEN" not in env
        assert run.call_args.kwargs["shell"] is False
        assert run.call_args.kwargs["encoding"] == "utf-8"
        assert run.call_args.kwargs["errors"] == "replace"

    def test_git_text_output_does_not_use_windows_locale(self, tmp_path: Path):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch("one_context.smart_commit.subprocess.run", return_value=completed) as run:
            _run_git(tmp_path, ["status"], timeout=10)
        assert run.call_args.kwargs["encoding"] == "utf-8"
        assert run.call_args.kwargs["errors"] == "replace"


class TestExecution:
    def test_submodule_commits_before_main_pointer(self, tmp_path: Path):
        child_remote = tmp_path / "child.git"
        subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", "--bare", str(child_remote)],
            check=True,
            capture_output=True,
        )
        seed = tmp_path / "seed"
        _init_repo(seed)
        (seed / "smart-commit").mkdir()
        (seed / "smart-commit" / "SKILL.md").write_text("# old\n", encoding="utf-8")
        _commit_all(seed)
        _git(seed, "remote", "add", "origin", str(child_remote))
        _git(seed, "push", "-u", "origin", "main")

        root = tmp_path / "workspace"
        _init_repo(root, "development")
        (root / "meta").mkdir()
        (root / "meta" / "agents.yaml").write_text("agents: []\n", encoding="utf-8")
        subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(child_remote),
                "skills",
            ],
            cwd=str(root),
            check=True,
            capture_output=True,
        )
        _git(root / "skills", "config", "user.name", "Test User")
        _git(root / "skills", "config", "user.email", "test@example.com")
        _commit_all(root)

        (root / "skills" / "smart-commit" / "SKILL.md").write_text("# new\n", encoding="utf-8")
        cli_dir = root / "packages" / "one-context"
        cli_dir.mkdir(parents=True)
        (cli_dir / "new.py").write_text("VALUE = 1\n", encoding="utf-8")

        config = SmartCommitConfig(
            messages={
                "skills": "feat(skills): test workflow",
                "main": "feat(cli): test workflow",
            }
        )
        plan = build_plan(root, mode="full", config=config, push=False)
        assert plan.safe, plan.blockers
        result = execute_plan(plan, push=False)

        by_name = {repo.name: repo for repo in result.repos}
        assert by_name["skills"].commit_hash
        assert by_name["main"].commit_hash
        assert _git(root, "status", "--porcelain") == ""
        assert _git(root / "skills", "status", "--porcelain") == ""
        assert _git(root, "ls-tree", "HEAD", "skills").split()[2] == by_name["skills"].commit_hash


def test_yes_gate_blocks_unknown_untracked_path(tmp_path: Path):
    _init_repo(tmp_path, "development")
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "agents.yaml").write_text("agents: []\n", encoding="utf-8")
    _commit_all(tmp_path)
    (tmp_path / "mystery.bin").write_text("unknown\n", encoding="utf-8")
    plan = build_plan(tmp_path, mode="main", config=SmartCommitConfig(), push=False)
    assert not plan.safe
    assert any("ASK mystery.bin" in blocker for blocker in plan.blockers)


def test_pre_staged_deletion_with_modification_commits_cleanly(tmp_path: Path):
    """A deletion pre-staged by `git rm` must not break staging of other paths.

    Regression: `git add --all -- <pathspec>` aborts with "pathspec did not match
    any files" when a pathspec targets only a deletion already removed from the
    index (`git rm` before smart-commit). The staged deletion must be skipped
    during re-staging while modifications beside it still land in the commit.
    """
    _init_repo(tmp_path, "development")
    (tmp_path / "keep.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "gone.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "agents.yaml").write_text("agents: []\n", encoding="utf-8")
    _commit_all(tmp_path)

    # Pre-stage a deletion, then modify another tracked file alongside it.
    subprocess.run(["git", "rm", "-f", "gone.md"], cwd=str(tmp_path), check=True, capture_output=True)
    (tmp_path / "keep.md").write_text("changed\n", encoding="utf-8")

    config = SmartCommitConfig(messages={"main": "fix: stage pre-staged deletion and modification"})
    plan = build_plan(
        tmp_path, mode="main", config=config, push=False, allow_delete=True,
    )
    assert plan.safe, plan.blockers

    result = execute_plan(plan, push=False)
    main_repo = {repo.name: repo for repo in result.repos}["main"]
    assert main_repo.commit_hash

    assert _git(tmp_path, "status", "--porcelain") == ""
    # The pre-staged deletion was actually committed (not silently dropped).
    committed = _git(tmp_path, "show", "--stat", "--name-only", "--format=", "HEAD")
    assert "gone.md" in committed
    assert "keep.md" in committed


def test_pre_staged_cached_deletion_keeps_ignored_local_file(tmp_path: Path):
    _init_repo(tmp_path, "development")
    (tmp_path / "local.exe").write_text("machine-only\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    _commit_all(tmp_path)

    (tmp_path / ".gitignore").write_text("local.exe\n", encoding="utf-8")
    subprocess.run(
        ["git", "rm", "--cached", "local.exe"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )

    config = SmartCommitConfig(messages={"main": "chore: stop tracking local executable"})
    plan = build_plan(
        tmp_path, mode="main", config=config, push=False, allow_delete=True,
    )
    assert plan.safe, plan.blockers

    result = execute_plan(plan, push=False)
    assert {repo.name: repo for repo in result.repos}["main"].commit_hash
    assert (tmp_path / "local.exe").is_file()
    assert _git(tmp_path, "status", "--porcelain") == ""


class TestRemoteAheadDetection:
    """Tests for the best-effort remote-ahead detection (change ①)."""

    @staticmethod
    def _set_push_plan(repo: RepoState, url: str) -> None:
        repo.push_plan = PushPlan(
            transport="ssh",
            url=url,
            branch=repo.target_branch,
            prefix=("git",),
            set_upstream=False,
        )

    def test_remote_ahead_sets_flag_and_error(self, tmp_path: Path):
        """When fetch reveals remote is ahead, remote_ahead=True and error is appended."""
        _init_repo(tmp_path)
        (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
        _commit_all(tmp_path)

        # Create a bare remote and push
        bare = tmp_path / "ahead-bare.git"
        subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", "--bare", str(bare)],
            check=True, capture_output=True,
        )
        _git(tmp_path, "remote", "add", "origin", str(bare))
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=str(tmp_path), capture_output=True, check=False,
        )

        # Simulate remote-ahead: create a new commit on the bare remote
        clone_dir = tmp_path / "clone"
        subprocess.run(
            ["git", "clone", str(bare), str(clone_dir)],
            check=True, capture_output=True,
        )
        _git(clone_dir, "config", "user.name", "Other")
        _git(clone_dir, "config", "user.email", "other@test")
        (clone_dir / "extra.txt").write_text("extra\n", encoding="utf-8")
        _commit_all(clone_dir, "add extra file")
        _git(clone_dir, "push", "origin", "main")

        # Now local tmp_path is behind remote
        repo = RepoState(
            "main", tmp_path, ".", True,
            changes=[Change("README.md", "M", ".M", True)],
            branch="main", target_branch="main",
            remote_url=str(bare),
        )
        config = SmartCommitConfig(transport="ssh")
        self._set_push_plan(repo, str(bare))
        _detect_remote_ahead(repo, config)

        assert repo.remote_ahead is True
        assert any("ahead of local HEAD" in e for e in repo.errors)

    def test_remote_not_ahead_does_not_set_flag(self, tmp_path: Path):
        """When local and remote are in sync, remote_ahead stays False."""
        _init_repo(tmp_path)
        (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
        _commit_all(tmp_path)

        bare = tmp_path / "sync-bare.git"
        subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", "--bare", str(bare)],
            check=True, capture_output=True,
        )
        _git(tmp_path, "remote", "add", "origin", str(bare))
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=str(tmp_path), capture_output=True, check=False,
        )
        # Ensure remote-tracking ref exists for the comparison
        local_head = _git(tmp_path, "rev-parse", "HEAD")
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", local_head],
            cwd=str(tmp_path), capture_output=True, check=True,
        )

        repo = RepoState(
            "main", tmp_path, ".", True,
            changes=[Change("README.md", "M", ".M", True)],
            branch="main", target_branch="main",
            remote_url=str(bare),
        )
        config = SmartCommitConfig(transport="ssh")
        self._set_push_plan(repo, str(bare))
        _detect_remote_ahead(repo, config)

        assert repo.remote_ahead is False
        assert not any("ahead" in e for e in repo.errors)

    def test_fetch_failure_does_not_block(self, tmp_path: Path):
        """When fetch fails (e.g. no network), remote_ahead stays False, no error."""
        repo = RepoState(
            "main", tmp_path, ".", True,
            changes=[Change("README.md", "M", ".M", True)],
            branch="main", target_branch="main",
            remote_url="git@github.com:nonexistent/repo.git",
        )
        config = SmartCommitConfig(transport="ssh", timeout_seconds=5)
        self._set_push_plan(repo, repo.remote_url)
        _detect_remote_ahead(repo, config)

        assert repo.remote_ahead is False
        assert not any("ahead" in e for e in repo.errors)


class TestPushErrorClassification:
    """Tests for push dry-run error classification (change ③)."""

    def test_non_fast_forward_classified_as_remote_ahead(self, tmp_path: Path):
        repo = RepoState(
            "skills", tmp_path, "skills", False,
            changes=[Change("SKILL.md", "M", ".M", True)],
            branch="main", target_branch="main",
            remote_url="git@github.com:owner/skills.git",
        )
        config = SmartCommitConfig(transport="ssh")
        failed = subprocess.CompletedProcess(
            [], 1, "",
            "! [rejected] HEAD -> refs/heads/main (non-fast-forward)\n"
            "error: failed to push some refs",
        )
        with patch("one_context.smart_commit._has_upstream", return_value=True), patch(
            "one_context.smart_commit._run_prefixed", return_value=failed
        ):
            plan = select_push_plan(repo, config)

        assert plan is None
        assert any("remote-ahead" in a for a in repo.push_attempts)

    def test_auth_failure_classified_as_credentials(self, tmp_path: Path):
        repo = RepoState(
            "skills", tmp_path, "skills", False,
            changes=[Change("SKILL.md", "M", ".M", True)],
            branch="main", target_branch="main",
            remote_url="git@github.com:owner/skills.git",
        )
        config = SmartCommitConfig(transport="https")
        failed = subprocess.CompletedProcess(
            [], 1, "",
            "fatal: could not read Username for 'https://github.com': No such device or address",
        )
        with patch("one_context.smart_commit.proxy_reachable", return_value=(True, "")), patch(
            "one_context.smart_commit._has_upstream", return_value=True
        ), patch(
            "one_context.smart_commit._run_prefixed", return_value=failed
        ):
            plan = select_push_plan(repo, config)

        assert plan is None
        assert any("auth/credentials" in a for a in repo.push_attempts)

    def test_generic_failure_uses_plain_label(self, tmp_path: Path):
        repo = RepoState(
            "skills", tmp_path, "skills", False,
            changes=[Change("SKILL.md", "M", ".M", True)],
            branch="main", target_branch="main",
            remote_url="git@github.com:owner/skills.git",
        )
        config = SmartCommitConfig(transport="ssh")
        failed = subprocess.CompletedProcess(
            [], 1, "", "fatal: some other error",
        )
        with patch("one_context.smart_commit._has_upstream", return_value=True), patch(
            "one_context.smart_commit._run_prefixed", return_value=failed
        ):
            plan = select_push_plan(repo, config)

        assert plan is None
        assert any(a.startswith("ssh: ") and "remote-ahead" not in a and "auth/credentials" not in a for a in repo.push_attempts)
