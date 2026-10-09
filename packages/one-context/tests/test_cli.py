"""CLI integration tests — exercise real argument parsing + dispatch."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from one_context import skills as skills_module
from one_context.cli import build_parser, main
from one_context.cli._meta import _cmd_sync
from one_context.sync import SyncResult


class TestBuildParser:
    def test_version_flag(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--version"])
        assert exc.value.code == 0

    def test_doctor_command(self):
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"

    def test_sync_command_no_select(self):
        parser = build_parser()
        args = parser.parse_args(["sync"])
        assert args.command == "sync"
        assert args.select == []

    def test_sync_with_select(self):
        parser = build_parser()
        args = parser.parse_args(["sync", "alpha", "beta"])
        assert args.select == ["alpha", "beta"]

    def test_sync_jobs_flag(self):
        parser = build_parser()
        args = parser.parse_args(["sync", "--jobs", "2"])
        assert args.jobs == 2

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--verbose", "doctor"])
        assert args.verbose is True

    def test_repo_list(self):
        parser = build_parser()
        args = parser.parse_args(["repo", "list"])
        assert args.command == "repo"
        assert args.repo_command == "list"

    def test_workspace_show(self):
        parser = build_parser()
        args = parser.parse_args(["workspace", "show", "dev"])
        assert args.ws_command == "show"
        assert args.id == "dev"

    def test_context_export(self):
        parser = build_parser()
        args = parser.parse_args(["context", "export", "dev", "--format", "markdown"])
        assert args.context_command == "export"
        assert args.format == "markdown"

    def test_context_export_compress_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "context",
                "export",
                "dev",
                "--format",
                "markdown",
                "--compress",
                "--target-tokens",
                "8000",
            ],
        )
        assert args.compress is True
        assert args.target_tokens == 8000

    def test_skills_list(self):
        parser = build_parser()
        args = parser.parse_args(["skills", "list"])
        assert args.command == "skills"
        assert args.skills_command == "list"

    def test_skills_install(self):
        parser = build_parser()
        args = parser.parse_args(["skills", "install", "info-radar", "gitsync"])
        assert args.command == "skills"
        assert args.skills_command == "install"
        assert args.names == ["info-radar", "gitsync"]
        assert args.all is False
        assert args.force is False

    def test_skills_install_force(self):
        parser = build_parser()
        args = parser.parse_args(["skills", "install", "--force", "info-radar"])
        assert args.force is True

    def test_skills_install_all(self):
        parser = build_parser()
        args = parser.parse_args(["skills", "install", "--all"])
        assert args.all is True

    def test_skills_uninstall(self):
        parser = build_parser()
        args = parser.parse_args(["skills", "uninstall", "info-radar"])
        assert args.skills_command == "uninstall"
        assert args.names == ["info-radar"]


def test_sync_command_returns_failure_when_any_repo_fails(tmp_path: Path):
    args = build_parser().parse_args(["sync"])
    results = [SyncResult("broken", False, "clone failed")]
    with patch("one_context.cli._meta.sync_repositories", return_value=results):
        assert _cmd_sync(tmp_path, args) == 1


class TestMainIntegration:
    def test_doctor_on_valid_root(self, tmp_root_with_workspaces: Path, monkeypatch: pytest.MonkeyPatch):
        # Create repo dirs with .git
        for name, cat in [("alpha", "develop"), ("beta", "research")]:
            d = tmp_root_with_workspaces / "repos" / cat / name
            d.mkdir(parents=True, exist_ok=True)
            (d / ".git").mkdir()

        monkeypatch.setattr(sys, "argv", ["onecxt", "--root", str(tmp_root_with_workspaces), "doctor"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

    def test_repo_list_on_valid_root(self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(sys, "argv", ["onecxt", "--root", str(tmp_root), "repo", "list"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out

    def test_workspace_list_no_file(self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(sys, "argv", ["onecxt", "--root", str(tmp_root), "workspace", "list"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "no workspaces" in out.lower()

    def test_profile_list_no_file(self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(sys, "argv", ["onecxt", "--root", str(tmp_root), "profile", "list"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "no profiles" in out.lower()

    def test_workspace_show(self, tmp_root_with_workspaces: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root_with_workspaces), "workspace", "show", "dev"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert '"workspace"' in out

    def test_context_export_json(self, tmp_root_with_workspaces: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root_with_workspaces), "context", "export", "dev"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert '"one-context"' in out

    def test_context_export_markdown(self, tmp_root_with_workspaces: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root_with_workspaces), "context", "export", "dev", "--format", "markdown"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "# one-context Context Export" in out

    def test_context_export_markdown_compress_tiny_budget(
        self, tmp_root_with_workspaces: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ):
        monkeypatch.setattr(
            sys, "argv",
            [
                "onecxt",
                "--root",
                str(tmp_root_with_workspaces),
                "context",
                "export",
                "dev",
                "--format",
                "markdown",
                "--target-tokens",
                "20",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "token budget" in out.lower()

    def test_invalid_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "argv", ["onecxt", "--root", str(tmp_path), "doctor"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    def test_sync_mock(self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root), "sync", "--jobs", "1"],
        )
        with patch("one_context.sync.subprocess.run") as mock_run:
            from unittest.mock import MagicMock
            mock_run.return_value = MagicMock(returncode=0)
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0

    def test_verbose_flag_enables_debug(self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch):
        import logging
        monkeypatch.setattr(sys, "argv", ["onecxt", "--verbose", "--root", str(tmp_root), "repo", "list"])
        with pytest.raises(SystemExit):
            main()
        logger = logging.getLogger("one_context")
        assert logger.level == logging.DEBUG

    def test_skills_list_empty(self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(sys, "argv", ["onecxt", "--root", str(tmp_root), "skills", "list"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Project skills" in out

    def test_skills_install_and_uninstall(
        self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path,
    ):
        # Prepare a fake project skill
        skill_dir = tmp_root / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\n---\n# Test Skill\n", encoding="utf-8")

        # Never let a test resolve the real user home on any platform.
        runtime_skills = tmp_path / ".claude" / "skills"
        monkeypatch.setitem(
            skills_module._TOOL_SKILLS_HOME,
            "claude",
            lambda: runtime_skills,
        )

        # Install
        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root), "skills", "install", "test-skill"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        installed = runtime_skills / "test-skill" / "SKILL.md"
        assert installed.is_file()

        # List should show it as installed
        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root), "skills", "list"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        out = capsys.readouterr().out
        assert "[x] test-skill" in out

        # Uninstall
        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root), "skills", "uninstall", "test-skill"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        assert not (runtime_skills / "test-skill").exists()

    def test_skills_install_codex_respects_codex_home(
        self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path,
    ):
        # Prepare a fake project skill
        skill_dir = tmp_root / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\n---\n# Test Skill\n", encoding="utf-8",
        )

        # Point CODEX_HOME at an isolated dir
        codex_home = tmp_path / "codex-home"
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root), "skills", "install",
             "--tool", "codex", "test-skill"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        installed = codex_home / "skills" / "test-skill" / "SKILL.md"
        assert installed.is_file()

        # Resolver uses CODEX_HOME without consulting the real user home.
        assert skills_module._codex_skills_home() == codex_home / "skills"

    def test_skills_install_force_replaces_runtime_bundle(
        self, tmp_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ):
        skill_dir = tmp_root / "skills" / "test-skill"
        references = skill_dir / "references"
        references.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\n---\n# Test Skill\n", encoding="utf-8",
        )
        (references / "old.md").write_text("old\n", encoding="utf-8")
        runtime_skills = tmp_path / ".claude" / "skills"
        monkeypatch.setitem(
            skills_module._TOOL_SKILLS_HOME,
            "claude",
            lambda: runtime_skills,
        )

        monkeypatch.setattr(
            sys, "argv",
            ["onecxt", "--root", str(tmp_root), "skills", "install", "test-skill"],
        )
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0

        installed = runtime_skills / "test-skill"
        (references / "old.md").unlink()
        (references / "new.md").write_text("new\n", encoding="utf-8")

        monkeypatch.setattr(
            sys, "argv",
            [
                "onecxt", "--root", str(tmp_root), "skills", "install",
                "--force", "test-skill",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0
        assert not (installed / "references" / "old.md").exists()
        assert (installed / "references" / "new.md").read_text(
            encoding="utf-8",
        ) == "new\n"
