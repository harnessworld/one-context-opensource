"""Tests for adapter compat-matrix: adapt-report.json + USER_EDITED + STALE/MISSING detection.

Coverage:
- ``adapters._report`` dataclass + digest + load/save round-trip
- ``GeneratedFile`` extension metadata (source_id/source_kind/status/.../source_digest)
- ``adapt`` emit loop: USER_EDITED skip, --force override, file_status field
- ``adapt --check`` tri-state (missing / stale / user_edited)
- opencode adapter emits per-asset metadata with non-empty report entries
- adapt-report.json saved at repo root with entry count matching generated files
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

# Trigger registration
import one_context.adapters.claude_code  # noqa: F401
import one_context.adapters.codex  # noqa: F401
import one_context.adapters.cursor  # noqa: F401
import one_context.adapters.hermes  # noqa: F401
import one_context.adapters.openclaw  # noqa: F401
import one_context.adapters.opencode  # noqa: F401
import one_context.adapters.pi  # noqa: F401

from one_context.adapters import GeneratedFile, get_adapter
from one_context.adapters._report import (
    AdaptReport,
    ReportEntry,
    REPORT_FILENAME,
    load_report,
    save_report,
    source_digest,
)
from one_context.adapters._shared_rules import GENERATED_MARKER


# ---------------------------------------------------------------------------
# _report module
# ---------------------------------------------------------------------------

class TestSourceDigest:
    def test_digest_is_12_chars(self):
        d = source_digest("hello world")
        assert len(d) == 12
        assert d == "b94d27b9934d"

    def test_digest_accepts_bytes(self):
        d_str = source_digest("hello")
        d_bytes = source_digest(b"hello")
        assert d_str == d_bytes

    def test_digest_stable_across_calls(self):
        assert source_digest("same input") == source_digest("same input")

    def test_digest_differs_on_different_input(self):
        assert source_digest("a") != source_digest("b")


class TestReportEntries:
    def test_entry_dataclass_serialization(self):
        e = ReportEntry(
            source_id="pm",
            source_kind="agent",
            target="opencode",
            target_kind="agent",
            status="native",
            path=".opencode/agent/pm.md",
            trigger="@pm",
            limitations=["no-op"],
            source_digest="abc",
            file_status="written",
            source_path="meta/agents.yaml#pm",
        )
        d = e.__dict__ if hasattr(e, "__dict__") else {
            "source_id": e.source_id,
            "source_kind": e.source_kind,
        }
        assert d["source_id"] == "pm"
        assert d["status"] == "native"

    def test_report_add_and_to_dict(self):
        r = AdaptReport(adapters=["opencode"])
        assert r.entries == []
        e = ReportEntry(
            source_id="x", source_kind="skill", target="opencode",
            target_kind="skill", status="native", path="x.md",
        )
        r.add(e)
        out = r.to_dict()
        assert out["version"] == "1"
        assert out["adapters"] == ["opencode"]
        assert len(out["entries"]) == 1
        assert out["entries"][0]["source_id"] == "x"


class TestReportIO:
    def test_save_load_round_trip(self, tmp_path: Path):
        r = AdaptReport(adapters=["opencode"])
        r.add(ReportEntry(
            source_id="pm", source_kind="agent", target="opencode",
            target_kind="agent", status="native", path=".opencode/agent/pm.md",
            trigger="@pm", source_digest="abc123",
        ))
        path = save_report(tmp_path, r)
        assert path.name == REPORT_FILENAME
        loaded = load_report(tmp_path)
        assert loaded is not None
        assert loaded.adapters == ["opencode"]
        assert len(loaded.entries) == 1
        assert loaded.entries[0].source_id == "pm"
        assert loaded.entries[0].source_digest == "abc123"

    def test_load_missing_returns_none(self, tmp_path: Path):
        assert load_report(tmp_path) is None

    def test_load_malformed_returns_none(self, tmp_path: Path):
        (tmp_path / REPORT_FILENAME).write_text("{not valid json", encoding="utf-8")
        assert load_report(tmp_path) is None


# ---------------------------------------------------------------------------
# GeneratedFile metadata
# ---------------------------------------------------------------------------

class TestGeneratedFileMetadata:
    def test_legacy_ctor_defaults(self):
        gf = GeneratedFile(rel_path="x", content="y", description="z")
        assert gf.source_id == ""
        assert gf.source_kind == ""
        assert gf.status == "native"
        assert gf.trigger == ""
        assert gf.limitations == ()
        assert gf.source_digest == ""

    def test_full_ctor_round_trip(self):
        gf = GeneratedFile(
            rel_path="x.md", content="y", description="z",
            source_id="pm", source_kind="agent",
            target_kind="agent", status="native",
            trigger="@pm", limitations=("a", "b"),
            source_digest="abc", source_path="meta/agents.yaml#pm",
        )
        assert gf.limitations == ("a", "b")
        assert gf.source_id == "pm"
        assert gf.source_kind == "agent"

    def test_frozen_dataclass_hashable(self):
        gf = GeneratedFile(rel_path="x", content="y", description="z")
        # frozen dataclass should be hashable (limitations as tuple)
        {gf}  # set membership works


# ---------------------------------------------------------------------------
# Emit loop: USER_EDITED / --force / file_status
# ---------------------------------------------------------------------------

@pytest.fixture()
def adapter_root_with_skills(tmp_path: Path) -> Path:
    """Minimal one-context tree with skills so adapt dev produces generated .md files."""
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "repos.yaml").write_text("repos: []\n", encoding="utf-8")
    (meta / "profiles.yaml").write_text(
        textwrap.dedent("""\
            profiles:
              - id: default-coding
                name: Default Coding
                description: test
                behavior:
                  plan_first: false
                  test_expectation: targeted
                  safety_level: standard
                  change_scope: focused
                output_style:
                  tone: concise
                  include_verification: true
        """),
        encoding="utf-8",
    )
    (meta / "workspaces.yaml").write_text(
        textwrap.dedent("""\
            workspaces:
              - id: dev
                name: Dev
                repos: []
                profiles:
                  - default-coding
                context:
                  summary: dev workspace
                  knowledge: []
        """),
        encoding="utf-8",
    )
    (meta / "agents.yaml").write_text("agents: []\n", encoding="utf-8")
    refs = tmp_path / "knowledge" / "references"
    refs.mkdir(parents=True)
    (refs / "project-overview.md").write_text("# Overview\n\n## What\ntest\n", encoding="utf-8")
    (refs / "skill-routing-policy.md").write_text("# Policy\n\nread SKILL.md\n", encoding="utf-8")
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo.\ntriggers:\n  - demo\n---\nbody\n",
        encoding="utf-8",
    )
    return tmp_path


class TestEmitUserEditedProtection:
    def test_first_run_writes_files(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev"])
        rc = _cmd_adapt(adapter_root_with_skills, args)
        assert rc == 0
        assert (adapter_root_with_skills / ".opencode" / "opencode.json").is_file()
        assert (adapter_root_with_skills / REPORT_FILENAME).is_file()

    def test_hand_edited_md_file_is_skipped(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev"])
        # First run
        _cmd_adapt(adapter_root_with_skills, args)
        # Tamper: remove marker from a generated md file → simulates hand-edit
        target = adapter_root_with_skills / ".opencode" / "instructions" / "onecxt-dev.md"
        # Move file out of the report registry by editing it without marker
        # AND remove its entry from adapt-report.json (simulate first-ever edit)
        target.write_text("# my hand edits\n\nuser added this\n", encoding="utf-8")
        report_path = adapter_root_with_skills / REPORT_FILENAME
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        rel = target.relative_to(adapter_root_with_skills).as_posix()
        raw["entries"] = [e for e in raw["entries"] if e["path"] != rel]
        report_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        # Second run — should skip the user-edited file
        rc = _cmd_adapt(adapter_root_with_skills, args)
        assert rc == 0  # exit 0; skipped, not failed
        # File should NOT be overwritten
        assert target.read_text(encoding="utf-8").startswith("# my hand edits")

    def test_force_overwrites_user_edited(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev"])
        _cmd_adapt(adapter_root_with_skills, args)
        target = adapter_root_with_skills / ".opencode" / "instructions" / "onecxt-dev.md"
        # Replace with content lacking marker, also remove from report registry
        target.write_text("# hand-edited content\n\nno marker here\n", encoding="utf-8")
        report_path = adapter_root_with_skills / REPORT_FILENAME
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        rel = target.relative_to(adapter_root_with_skills).as_posix()
        raw["entries"] = [e for e in raw["entries"] if e["path"] != rel]
        report_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        # Should be user-edited → skipped without --force
        _cmd_adapt(adapter_root_with_skills, args)
        assert target.read_text(encoding="utf-8").startswith("# hand-edited content")
        # Now with --force, should be regenerated
        args_force = parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev", "--force"])
        rc = _cmd_adapt(adapter_root_with_skills, args_force)
        assert rc == 0
        post = target.read_text(encoding="utf-8")
        # On --force, the generated content (with marker) overwrites
        assert "GENERATED by onecxt adapt" in post or GENERATED_MARKER in post

    def test_skipped_report_entry_never_transfers_ownership(
        self, adapter_root_with_skills: Path
    ):
        from one_context.cli._adapt import _detect_user_edited

        rel_path = ".opencode/opencode.json"
        existing = '{"user": true}\n'
        (adapter_root_with_skills / REPORT_FILENAME).write_text(
            json.dumps({
                "entries": [{
                    "path": rel_path,
                    "file_status": "skipped_user_edited",
                }]
            }),
            encoding="utf-8",
        )

        assert _detect_user_edited(
            adapter_root_with_skills,
            rel_path,
            existing,
        ) is True

    def test_written_report_entry_never_transfers_ownership(
        self, adapter_root_with_skills: Path
    ):
        from one_context.cli._adapt import _detect_user_edited

        rel_path = ".opencode/opencode.json"
        existing = '{"user": true}\n'
        (adapter_root_with_skills / REPORT_FILENAME).write_text(
            json.dumps({
                "entries": [{
                    "path": rel_path,
                    "file_status": "written",
                }]
            }),
            encoding="utf-8",
        )

        assert _detect_user_edited(
            adapter_root_with_skills,
            rel_path,
            existing,
        ) is True

    def test_exact_cached_hash_proves_markerless_ownership(
        self, adapter_root_with_skills: Path
    ):
        from one_context.cache import file_hash, save_cache
        from one_context.cli._adapt import _detect_user_edited

        rel_path = ".opencode/opencode.json"
        existing = '{"generated": true}\n'
        save_cache(adapter_root_with_skills, {rel_path: file_hash(existing)})

        assert _detect_user_edited(
            adapter_root_with_skills,
            rel_path,
            existing,
        ) is False
        assert _detect_user_edited(
            adapter_root_with_skills,
            rel_path,
            '{"user": true}\n',
        ) is True

    def test_cached_output_is_recreated_when_disk_file_is_missing(
        self, adapter_root_with_skills: Path
    ):
        from one_context.cli import _cmd_adapt, build_parser

        parser = build_parser()
        args = parser.parse_args([
            "--root",
            str(adapter_root_with_skills),
            "adapt",
            "dev",
            "--only",
            "opencode",
        ])
        assert _cmd_adapt(adapter_root_with_skills, args) == 0
        target = adapter_root_with_skills / ".opencode/opencode.json"
        target.unlink()

        assert _cmd_adapt(adapter_root_with_skills, args) == 0
        assert target.is_file()


class TestAdaptCheckTriState:
    def test_check_passes_when_fresh(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        _cmd_adapt(adapter_root_with_skills,
                   parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev"]))
        rc = _cmd_adapt(adapter_root_with_skills,
                        parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev", "--check"]))
        assert rc == 0

    def test_check_reports_missing(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        # No prior write — files are missing
        rc = _cmd_adapt(adapter_root_with_skills,
                        parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev", "--check"]))
        assert rc == 1

    def test_check_reports_stale_when_tracked_file_differs(
        self, adapter_root_with_skills: Path, capsys: pytest.CaptureFixture
    ):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        _cmd_adapt(adapter_root_with_skills,
                   parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev"]))
        target = adapter_root_with_skills / ".opencode" / "instructions" / "onecxt-dev.md"
        existing = target.read_text(encoding="utf-8")
        target.write_text(existing + "\n# stale generated output\n", encoding="utf-8")
        rc = _cmd_adapt(adapter_root_with_skills,
                        parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev", "--check"]))
        assert rc == 1
        captured = capsys.readouterr()
        assert "stale" in captured.err.lower()

    def test_check_reports_user_edited_when_untracked_hand_edit(
        self, adapter_root_with_skills: Path, capsys: pytest.CaptureFixture
    ):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        _cmd_adapt(adapter_root_with_skills,
                   parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev"]))
        # Hand-edit: replace file content + remove from prior report
        target = adapter_root_with_skills / ".opencode" / "instructions" / "onecxt-dev.md"
        target.write_text("hand-edit-no-marker\n", encoding="utf-8")
        report_path = adapter_root_with_skills / REPORT_FILENAME
        raw = json.loads(report_path.read_text(encoding="utf-8"))
        rel = target.relative_to(adapter_root_with_skills).as_posix()
        raw["entries"] = [e for e in raw["entries"] if e["path"] != rel]
        report_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        rc = _cmd_adapt(adapter_root_with_skills,
                        parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev", "--check"]))
        assert rc == 1
        captured = capsys.readouterr()
        assert "user_edited" in captured.err.lower()


# ---------------------------------------------------------------------------
# OpenCode adapter metadata output
# ---------------------------------------------------------------------------

class TestOpenCodeAdapterMetadata:
    def test_workspace_file_carries_profile_metadata(self, adapter_root_with_skills: Path):
        from one_context.context import build_workspace_context
        ctx = build_workspace_context(adapter_root_with_skills, "dev")
        ws = ctx["workspace"]
        adapter = get_adapter("opencode")
        files = adapter.generate(adapter_root_with_skills, ws, ctx)
        assert any(f.rel_path == ".opencode/instructions/onecxt-dev.md" for f in files)
        ws_file = next(f for f in files if f.rel_path.endswith("onecxt-dev.md"))
        assert ws_file.source_id == "dev"
        assert ws_file.source_kind == "profile"
        assert ws_file.target_kind == "instructions"
        assert ws_file.status == "native"
        assert ws_file.trigger
        assert ws_file.source_path

    def test_skill_file_carries_skill_metadata(self, adapter_root_with_skills: Path):
        from one_context.skills import discover_skills
        skills = discover_skills(adapter_root_with_skills)
        adapter = get_adapter("opencode")
        files = adapter.generate_skills(adapter_root_with_skills, skills)
        assert files
        f = files[0]
        assert f.source_id == "demo"
        assert f.source_kind == "skill"
        assert f.target_kind == "skill"
        assert f.status == "native"
        assert f.trigger  # non-empty
        assert f.source_digest  # 12 chars computed from SKILL.md content
        assert f.source_path == "skills/demo/SKILL.md"

    def test_opencode_json_aggregate_status(self, adapter_root_with_skills: Path):
        adapter = get_adapter("opencode")
        files = adapter.generate_project_artifacts(adapter_root_with_skills, ["dev"], [])
        oc_file = next(f for f in files if f.rel_path.endswith("opencode.json"))
        assert oc_file.status == "aggregate"
        data = json.loads(oc_file.content)
        # x-onecxt-generated must NOT appear (OpenCode rejects unknown keys)
        assert "x-onecxt-generated" not in data
        # Must still only have schema-valid keys
        assert set(data.keys()).issubset({"$schema", "instructions", "skills"})

    def test_opencode_json_has_no_inline_marker(self, adapter_root_with_skills: Path):
        """opencode.json cannot carry an HTML comment marker; verify."""
        adapter = get_adapter("opencode")
        files = adapter.generate_project_artifacts(adapter_root_with_skills, ["dev"], [])
        oc_file = next(f for f in files if f.rel_path.endswith("opencode.json"))
        # JSON has no comment syntax — marker only via limitations text in report
        assert "<!--" not in oc_file.content
        # But the limitations must explicitly state this
        assert any("marker" in lim.lower() for lim in oc_file.limitations)


# ---------------------------------------------------------------------------
# End-to-end adapt run: adapt-report.json contents
# ---------------------------------------------------------------------------

class TestAdaptReportContents:
    def test_report_lists_all_generated_files(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev", "--only", "opencode"])
        rc = _cmd_adapt(adapter_root_with_skills, args)
        assert rc == 0
        raw = json.loads((adapter_root_with_skills / REPORT_FILENAME).read_text(encoding="utf-8"))
        assert raw["version"] == "1"
        assert "opencode" in raw["adapters"]
        # At least: 1 instructions file, 1 skill, 1 opencode.json, AGENTS.md, ...
        paths = {e["path"] for e in raw["entries"]}
        assert ".opencode/opencode.json" in paths
        assert ".opencode/instructions/onecxt-dev.md" in paths
        assert ".opencode/skills/demo/SKILL.md" in paths

    def test_pi_report_lists_native_and_translated_surfaces(
        self, adapter_root_with_skills: Path
    ):
        from one_context.cli import build_parser, _cmd_adapt

        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root_with_skills),
            "adapt", "dev", "--only", "pi",
        ])
        assert _cmd_adapt(adapter_root_with_skills, args) == 0

        raw = json.loads(
            (adapter_root_with_skills / REPORT_FILENAME).read_text(encoding="utf-8")
        )
        assert raw["adapters"] == ["pi"]
        by_path = {
            entry["path"]: entry for entry in raw["entries"] if entry["path"]
        }
        assert by_path[".pi/APPEND_SYSTEM.md"]["status"] == "aggregate"
        assert by_path[".agents/skills/demo/SKILL.md"]["status"] == "native"
        assert raw["probes"][0]["adapter"] == "pi"

    def test_codex_only_shared_skill_has_codex_report_semantics(
        self, adapter_root_with_skills: Path
    ):
        from one_context.cli import _cmd_adapt, build_parser

        parser = build_parser()
        args = parser.parse_args([
            "--root",
            str(adapter_root_with_skills),
            "adapt",
            "dev",
            "--only",
            "codex",
        ])
        assert _cmd_adapt(adapter_root_with_skills, args) == 0
        raw = json.loads(
            (adapter_root_with_skills / REPORT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        shared_skill = next(
            entry
            for entry in raw["entries"]
            if entry["path"] == ".agents/skills/demo/SKILL.md"
        )
        assert shared_skill["target"] == "codex"
        assert shared_skill["targets"] == ["codex"]
        assert not any("Pi" in item for item in shared_skill["limitations"])

    def test_report_entries_have_file_status(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root_with_skills), "adapt", "dev", "--only", "opencode"])
        _cmd_adapt(adapter_root_with_skills, args)
        raw = json.loads((adapter_root_with_skills / REPORT_FILENAME).read_text(encoding="utf-8"))
        statuses = {e["file_status"] for e in raw["entries"]}
        assert statuses
        assert all(s in {"written", "cached", "skipped_user_edited", "dry_run", ""} for s in statuses)


# ---------------------------------------------------------------------------
# Spec AC7: unsupported assets must appear in the report
# ---------------------------------------------------------------------------

class TestUnsupportedAssetsAD7:
    def test_opencode_unsupported_entries_explicit(self, adapter_root_with_skills: Path):
        """AC7: OpenCode adapter reports unsupported commands, not silent skip."""
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root_with_skills),
            "adapt", "dev", "--only", "opencode",
        ])
        _cmd_adapt(adapter_root_with_skills, args)
        raw = json.loads((adapter_root_with_skills / REPORT_FILENAME).read_text(encoding="utf-8"))
        unsupported = [e for e in raw["entries"] if e["status"] == "unsupported"]
        assert unsupported, "no unsupported entries found — AC7 not satisfied"
        # OpenCode declares exactly one unsupported kind: command.
        # Rules are distributed uniformly across all adapters (inlined), so
        # "rule" must NOT appear as unsupported.
        unsupported_kinds = {e["source_kind"] for e in unsupported}
        assert unsupported_kinds == {"command"}

    def test_unsupported_entries_have_non_empty_limitations(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root_with_skills),
            "adapt", "dev", "--only", "opencode",
        ])
        _cmd_adapt(adapter_root_with_skills, args)
        raw = json.loads((adapter_root_with_skills / REPORT_FILENAME).read_text(encoding="utf-8"))
        for e in raw["entries"]:
            if e["status"] != "unsupported":
                continue
            assert e["limitations"], f"unsupported entry {e['source_id']} has empty limitations"
            # All limitations must be non-empty strings
            assert all(
                isinstance(limitation, str) and limitation
                for limitation in e["limitations"]
            )

    def test_unsupported_entries_do_not_write_files(self, adapter_root_with_skills: Path):
        """AC7: unsupported placeholders must NOT cause files to appear on disk."""
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root_with_skills),
            "adapt", "dev", "--only", "opencode",
        ])
        _cmd_adapt(adapter_root_with_skills, args)
        # Read report entries marked unsupported — verify each has empty path or path == ""
        raw = json.loads((adapter_root_with_skills / REPORT_FILENAME).read_text(encoding="utf-8"))
        for e in raw["entries"]:
            if e["status"] == "unsupported":
                assert e["path"] == "", \
                    f"unsupported entry {e['source_id']} should have empty path, got {e['path']!r}"


# ---------------------------------------------------------------------------
# Phase 2: All adapters instrumented with metadata
# ---------------------------------------------------------------------------

class TestCapabilityProbeAC8:
    """AC8: Capability probe — best-effort probe of target CLI installation.

    The probe must:
    - NEVER raise (must degrade gracefully to ProbeResult.available=False)
    - NEVER block generation (failed probes only emit stderr + a ``probes[]``
      entry in the report)
    - Return populated version when the CLI is installed
    """

    def test_probe_known_binary_present_or_absent(self):
        """Known CLI targets return ProbeResult with available=True|False."""
        from one_context.adapters._report import ProbeResult, probe_cli
        for aname in ("opencode", "claude_code", "codex", "pi"):
            r = probe_cli(aname)
            assert isinstance(r, ProbeResult)
            assert r.adapter == aname
            # available is bool, version is str (empty when not installed)
            assert isinstance(r.available, bool)
            assert isinstance(r.version, str)
            assert isinstance(r.error, str)
            if r.available:
                assert r.version
            else:
                assert r.error  # non-empty error when not available

    def test_probe_unknown_adapter_returns_error_not_raises(self):
        from one_context.adapters._report import probe_cli
        # Unknown adapter name → ProbeResult.available=False with reason
        r = probe_cli("does-not-exist")
        assert r.available is False
        assert "does-not-exist" in r.error

    def test_probe_results_in_adapt_report(self, adapter_root_with_skills: Path):
        from one_context.cli import build_parser, _cmd_adapt
        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root_with_skills),
            "adapt", "dev", "--only", "opencode",
        ])
        rc = _cmd_adapt(adapter_root_with_skills, args)
        assert rc == 0
        raw = json.loads((adapter_root_with_skills / REPORT_FILENAME).read_text(encoding="utf-8"))
        assert "probes" in raw
        assert len(raw["probes"]) >= 1
        for p in raw["probes"]:
            assert p["adapter"]
            assert isinstance(p["available"], bool)
            assert isinstance(p["version"], str)

    @pytest.mark.parametrize("adapter_name", ["opencode", "pi"])
    def test_probe_failure_does_not_block_generation(
        self,
        adapter_root_with_skills: Path,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        adapter_name: str,
    ):
        """When the target CLI is not on PATH, generation still succeeds
        (exit 0) and the failure is logged to stderr + probes[] entry."""
        from one_context.cli import build_parser, _cmd_adapt
        import shutil

        # Force the selected binary missing while leaving other probes alone.
        original_which = shutil.which
        monkeypatch.setattr(
            shutil,
            "which",
            lambda binary: (
                None if binary == adapter_name else original_which(binary)
            ),
        )
        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root_with_skills),
            "adapt", "dev", "--only", adapter_name,
        ])
        rc = _cmd_adapt(adapter_root_with_skills, args)
        assert rc == 0  # generation succeeded despite probe failure
        captured = capsys.readouterr()
        assert f"probe: {adapter_name}" in captured.err

    @pytest.mark.parametrize("adapter_name", [
        "claude_code", "codex", "cursor", "openclaw", "hermes", "opencode",
    ])
    def test_workspace_file_has_metadata(self, adapter_root_with_skills: Path, adapter_name: str):
        from one_context.context import build_workspace_context
        ctx = build_workspace_context(adapter_root_with_skills, "dev")
        ws = ctx["workspace"]
        adapter = get_adapter(adapter_name)
        files = adapter.generate(adapter_root_with_skills, ws, ctx)
        # At least one workspace-level file must exist with full metadata
        meta_files = [
            f for f in files
            if f.source_id and f.source_kind and f.status and f.trigger
        ]
        assert meta_files, (
            f"{adapter_name} produced no GeneratedFile with full metadata "
            f"(source_id/source_kind/status/trigger)"
        )

    @pytest.mark.parametrize("adapter_name", [
        "claude_code", "cursor", "openclaw", "hermes", "opencode",
    ])
    def test_skill_file_has_metadata_and_digest(
        self, adapter_root_with_skills: Path, adapter_name: str
    ):
        from one_context.skills import discover_skills
        skills = discover_skills(adapter_root_with_skills)
        adapter = get_adapter(adapter_name)
        files = adapter.generate_skills(adapter_root_with_skills, skills)
        # All adapter skill files must carry source_id + source_kind="skill" + source_digest
        for f in files:
            assert f.source_id, f"{adapter_name}: skill file {f.rel_path} missing source_id"
            assert f.source_kind == "skill", \
                f"{adapter_name}: skill file {f.rel_path} source_kind={f.source_kind!r} (expected 'skill')"
            assert f.source_digest and len(f.source_digest) == 12, \
                f"{adapter_name}: skill file {f.rel_path} missing/invalid source_digest"
            assert f.source_path, \
                f"{adapter_name}: skill file {f.rel_path} missing source_path"

    def test_shared_codex_pi_skill_has_metadata_and_digest(
        self, adapter_root_with_skills: Path
    ):
        from one_context.adapters._agent_skills import generate_shared_agent_skills
        from one_context.skills import discover_skills

        files = generate_shared_agent_skills(
            adapter_root_with_skills,
            discover_skills(adapter_root_with_skills),
        )
        assert files
        for generated in files:
            assert generated.rel_path.startswith(".agents/skills/")
            assert generated.source_kind == "skill"
            assert generated.status == "native"
            assert len(generated.source_digest) == 12
            assert generated.source_path

    @pytest.mark.parametrize("adapter_name", [
        "claude_code", "codex", "openclaw", "hermes", "opencode", "pi",
    ])
    def test_project_artifact_has_aggregate_status(
        self, adapter_root_with_skills: Path, adapter_name: str
    ):
        """Each root-aggregating adapter's root artifact (CLAUDE.md / .hermes.md /
        opencode.json / onecxt-project.json) should be marked status=aggregate
        since it consolidates multiple canonical sources into a single file.

        Cursor is excluded — it has no project-root artifact (relies on
        per-rule MDC files each with their own frontmatter; no aggregation).
        """
        from one_context.agents import load_agents
        adapter = get_adapter(adapter_name)
        # Simulate generate() having run first (some adapters collect state)
        from one_context.context import build_workspace_context
        ctx = build_workspace_context(adapter_root_with_skills, "dev")
        adapter.generate(adapter_root_with_skills, ctx["workspace"], ctx)
        from one_context.skills import discover_skills
        skills = discover_skills(adapter_root_with_skills)
        adapter.generate_skills(adapter_root_with_skills, skills)
        agents, _ = load_agents(adapter_root_with_skills)
        files = adapter.generate_project_artifacts(adapter_root_with_skills, ["dev"], agents)
        root_files = [
            f for f in files
            if f.rel_path in {
                "CLAUDE.md", ".hermes.md",
                ".codex/README.md",
                ".opencode/opencode.json",
                ".openclaw/onecxt-project.json",
                ".pi/APPEND_SYSTEM.md",
            }
        ]
        assert root_files, f"{adapter_name} produced no recognized root artifact"
        for f in root_files:
            assert f.status in {"aggregate", "translate"}, \
                f"{adapter_name} root {f.rel_path} status={f.status!r} (expected aggregate or translate)"
            assert f.source_id, f"{adapter_name} root {f.rel_path} missing source_id"
            assert f.source_kind in {"manifest", "overview", "rule"}, \
                f"{adapter_name} root {f.rel_path} source_kind={f.source_kind!r}"
