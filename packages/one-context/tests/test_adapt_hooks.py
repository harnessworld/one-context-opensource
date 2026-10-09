"""Tests for onecxt adapt-install / adapt-uninstall commands."""

from __future__ import annotations

import os
import shlex
import stat
import textwrap
from pathlib import Path

import pytest

from one_context.cli import build_parser, _cmd_adapt_install, _cmd_adapt_uninstall


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal one-context tree inside a git repo."""
    import subprocess

    # Init git repo
    subprocess.check_call(
        ["git", "init", "-b", "main"],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), stdout=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), stdout=subprocess.DEVNULL,
    )

    # Minimal one-context structure
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "repos.yaml").write_text("repos: []\n", encoding="utf-8")
    (meta / "profiles.yaml").write_text(
        textwrap.dedent("""\
            profiles:
              - id: default-coding
                name: Default Coding
                mode: edit
        """),
        encoding="utf-8",
    )
    (meta / "workspaces.yaml").write_text(
        textwrap.dedent("""\
            workspaces:
              - id: dev
                name: Development
                profiles:
                  - default-coding
        """),
        encoding="utf-8",
    )
    (meta / "agents.yaml").write_text(
        "version: '1'\nagents: []\n", encoding="utf-8",
    )
    return tmp_path


class TestAdaptInstall:
    def test_installs_hooks(self, git_repo: Path):
        args = build_parser().parse_args(["--root", str(git_repo), "adapt-install"])
        rc = _cmd_adapt_install(git_repo, args)
        assert rc == 0

        # Check post-checkout hook
        hook = git_repo / ".git" / "hooks" / "post-checkout"
        assert hook.is_file()
        content = hook.read_text(encoding="utf-8")
        assert "-m one_context" in content
        assert "adapt --all" in content
        # Windows does not expose the POSIX executable bit used by Git hooks.
        if os.name != "nt":
            assert hook.stat().st_mode & stat.S_IXUSR

    def test_installs_post_merge_hook(self, git_repo: Path):
        args = build_parser().parse_args(["--root", str(git_repo), "adapt-install"])
        _cmd_adapt_install(git_repo, args)

        hook = git_repo / ".git" / "hooks" / "post-merge"
        assert hook.is_file()
        content = hook.read_text(encoding="utf-8")
        assert "-m one_context" in content
        assert "adapt --all" in content

    def test_idempotent(self, git_repo: Path):
        args = build_parser().parse_args(["--root", str(git_repo), "adapt-install"])
        _cmd_adapt_install(git_repo, args)

        # Run again — should skip, not duplicate
        _cmd_adapt_install(git_repo, args)

        content = (git_repo / ".git" / "hooks" / "post-checkout").read_text(encoding="utf-8")
        assert content.count("auto-adapt-command") == 1

    def test_appends_to_existing_hook(self, git_repo: Path):
        hook_path = git_repo / ".git" / "hooks" / "post-checkout"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IXUSR)

        args = build_parser().parse_args(["--root", str(git_repo), "adapt-install"])
        _cmd_adapt_install(git_repo, args)

        content = hook_path.read_text(encoding="utf-8")
        assert "echo existing" in content
        assert "auto-adapt-command" in content

    def test_quotes_paths_with_spaces(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch):
        fake_python = git_repo / "Program Files" / "Python" / "python.exe"
        monkeypatch.setattr("one_context.cli._adapt.sys.executable", str(fake_python))
        args = build_parser().parse_args(["--root", str(git_repo), "adapt-install"])
        _cmd_adapt_install(git_repo, args)

        content = (git_repo / ".git" / "hooks" / "post-checkout").read_text(encoding="utf-8")
        expected = shlex.quote(fake_python.resolve().as_posix())
        assert f"{expected} -m one_context" in content

    def test_prepends_shebang_and_preserves_existing_precommit(self, git_repo: Path):
        hook_path = git_repo / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("echo existing\n", encoding="utf-8")

        args = build_parser().parse_args(["--root", str(git_repo), "adapt-install"])
        assert _cmd_adapt_install(git_repo, args) == 0

        content = hook_path.read_text(encoding="utf-8")
        assert content.startswith("#!/bin/sh\n\necho existing\n")
        assert "# one-context: pre-commit adapt-guard" in content


class TestAdaptUninstall:
    def test_removes_hooks(self, git_repo: Path):
        # Install first
        args = build_parser().parse_args(["--root", str(git_repo), "adapt-install"])
        _cmd_adapt_install(git_repo, args)

        # Uninstall
        args = build_parser().parse_args(["--root", str(git_repo), "adapt-uninstall"])
        rc = _cmd_adapt_uninstall(git_repo, args)
        assert rc == 0

        # post-checkout should be deleted (only had our content)
        hook = git_repo / ".git" / "hooks" / "post-checkout"
        assert not hook.is_file()

    def test_removes_lines_keeps_other_content(self, git_repo: Path):
        hook_path = git_repo / ".git" / "hooks" / "post-checkout"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(
            "#!/bin/sh\necho existing\n\n# one-context: auto-adapt on pull/checkout\nonecxt adapt --all\n",
            encoding="utf-8",
        )

        args = build_parser().parse_args(["--root", str(git_repo), "adapt-uninstall"])
        _cmd_adapt_uninstall(git_repo, args)

        content = hook_path.read_text(encoding="utf-8")
        assert "onecxt adapt" not in content
        assert "echo existing" in content

    def test_no_hooks_to_remove(self, git_repo: Path):
        args = build_parser().parse_args(["--root", str(git_repo), "adapt-uninstall"])
        rc = _cmd_adapt_uninstall(git_repo, args)
        assert rc == 0
