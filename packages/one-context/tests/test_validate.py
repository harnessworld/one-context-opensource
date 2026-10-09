"""Tests for one_context.validate — doctor() cross-checks."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from one_context.validate import (
    DoctorResult,
    doctor,
    _collect_all_knowledge_references,
    _expand_directory_refs,
    _check_at_references,
    generate_knowledge_graph,
    AT_REF_PATTERN,
)


class TestDoctor:
    def test_clean_state(self, tmp_root_with_workspaces: Path):
        # Create repo dirs with .git so doctor sees them
        for name in ("alpha", "beta"):
            d = tmp_root_with_workspaces / "repos" / ("develop" if name == "alpha" else "research") / name
            d.mkdir(parents=True, exist_ok=True)
            (d / ".git").mkdir()
        # Create the project rules file so doctor doesn't warn about its absence
        rules_dir = tmp_root_with_workspaces / ".onecxt"
        rules_dir.mkdir(exist_ok=True)
        (rules_dir / "RULES.md").write_text("# Project Rules\n", encoding="utf-8")
        result = doctor(tmp_root_with_workspaces)
        assert result.errors == []
        assert result.warnings == []

    def test_missing_repo_dir_warning(self, tmp_root_with_workspaces: Path):
        result = doctor(tmp_root_with_workspaces)
        assert any("local path missing" in w for w in result.warnings)

    def test_not_git_repo_warning(self, tmp_root_with_workspaces: Path):
        d = tmp_root_with_workspaces / "repos" / "develop" / "alpha"
        d.mkdir(parents=True)
        # no .git directory
        result = doctor(tmp_root_with_workspaces)
        assert any("not a git repo" in w for w in result.warnings)

    def test_repo_path_case_mismatch_warning(self, tmp_root: Path):
        actual = tmp_root / "repos" / "animation" / "GSAP"
        actual.mkdir(parents=True)
        (actual / ".git").mkdir()
        (tmp_root / "meta" / "repos.yaml").write_text(
            textwrap.dedent("""\
                repos:
                  - url: https://github.com/greensock/GSAP
                    id: gsap
                    path: repos/animation/gsap
            """),
            encoding="utf-8",
        )

        result = doctor(tmp_root)

        assert any("path case mismatch" in warning for warning in result.warnings)

    def test_unknown_repo_in_workspace(self, tmp_root: Path):
        (tmp_root / "meta" / "workspaces.yaml").write_text(
            textwrap.dedent("""\
                workspaces:
                  - id: ws1
                    repos:
                      - nonexistent-repo
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("unknown repo id" in e for e in result.errors)

    def test_feature_index_must_cover_each_spec_once(self, tmp_root: Path):
        features = tmp_root / "features"
        indexed = features / "core" / "indexed"
        unindexed = features / "core" / "unindexed"
        indexed.mkdir(parents=True)
        unindexed.mkdir(parents=True)
        (indexed / "spec.md").write_text("# Indexed\n", encoding="utf-8")
        (unindexed / "spec.md").write_text("# Unindexed\n", encoding="utf-8")
        (features / "INDEX.md").write_text(
            "| id | title | category | status | path | primary_repo_id |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| indexed | Indexed | core | draft | `features/core/indexed/` | one-context |\n",
            encoding="utf-8",
        )

        result = doctor(tmp_root)

        assert any(
            "spec.md is missing from INDEX: features/core/unindexed" in error
            for error in result.errors
        )

    def test_feature_index_rejects_duplicate_path(self, tmp_root: Path):
        features = tmp_root / "features"
        feature = features / "core" / "demo"
        feature.mkdir(parents=True)
        (feature / "spec.md").write_text("# Demo\n", encoding="utf-8")
        (features / "INDEX.md").write_text(
            "| id | title | category | status | path | primary_repo_id |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| demo | Demo | core | draft | `features/core/demo/` | one-context |\n"
            "| demo-copy | Demo | core | draft | `features/core/demo/` | one-context |\n",
            encoding="utf-8",
        )

        result = doctor(tmp_root)

        assert any("duplicate INDEX path" in error for error in result.errors)

    def test_feature_index_ignores_source_snapshots_but_checks_archived_specs(
        self, tmp_root: Path
    ):
        features = tmp_root / "features"
        feature = features / "teachback" / "archive" / "demo"
        snapshot = feature / "production" / "content" / "source" / "prior-design"
        unindexed = features / "teachback" / "archive" / "unindexed"
        for directory in (feature, snapshot, unindexed):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "spec.md").write_text("# Spec\n", encoding="utf-8")
        (features / "INDEX.md").write_text(
            "| demo | Demo | teachback | archived | "
            "`features/teachback/archive/demo/` | one-context |\n",
            encoding="utf-8",
        )

        result = doctor(tmp_root)

        assert result.errors == [
            "features: spec.md is missing from INDEX: features/teachback/archive/unindexed"
        ]

    def test_unknown_profile_in_workspace(self, tmp_root: Path):
        (tmp_root / "meta" / "workspaces.yaml").write_text(
            textwrap.dedent("""\
                workspaces:
                  - id: ws1
                    repos:
                      - alpha
                    profiles:
                      - nonexistent-profile
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("unknown profile id" in e for e in result.errors)

    def test_bad_repos_yaml_returns_error(self, tmp_path: Path):
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "repos.yaml").write_text("not valid", encoding="utf-8")
        result = doctor(tmp_path)
        assert len(result.errors) > 0

    def test_bad_workspaces_yaml_error(self, tmp_root: Path):
        (tmp_root / "meta" / "workspaces.yaml").write_text(
            "- just a list\n", encoding="utf-8"
        )
        result = doctor(tmp_root)
        assert any("mapping" in e for e in result.errors)

    def test_bad_profiles_yaml_error(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            "- just a list\n", encoding="utf-8"
        )
        result = doctor(tmp_root)
        assert any("mapping" in e for e in result.errors)

    def test_workspace_repos_not_list(self, tmp_root: Path):
        (tmp_root / "meta" / "workspaces.yaml").write_text(
            textwrap.dedent("""\
                workspaces:
                  - id: ws1
                    repos: "not-a-list"
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("'repos' must be a list" in e for e in result.errors)

    def test_workspace_profiles_not_list(self, tmp_root: Path):
        (tmp_root / "meta" / "workspaces.yaml").write_text(
            textwrap.dedent("""\
                workspaces:
                  - id: ws1
                    repos:
                      - alpha
                    profiles: "not-a-list"
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("'profiles' must be a list" in e for e in result.errors)

    def test_insteadof_conflict_detected(self, tmp_root: Path):
        """Doctor detects mutually-conflicting url.*.insteadOf rules."""
        import subprocess

        # Set up conflicting rules: https→ssh and ssh→https
        subprocess.run(
            ["git", "config", "--global", "url.git@github.com:.insteadof", "https://github.com/"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "--global", "url.https://github.com/.insteadof", "git@github.com:"],
            check=True, capture_output=True,
        )
        try:
            result = doctor(tmp_root)
            assert any("insteadOf conflict" in e for e in result.errors)
        finally:
            # Clean up the test rules
            subprocess.run(
                ["git", "config", "--global", "--unset", "url.git@github.com:.insteadof"],
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "--global", "--unset", "url.https://github.com/.insteadof"],
                capture_output=True,
            )


class TestDoctorInheritance:
    """Doctor checks for extends / mixins validation."""

    def test_extends_unknown_profile(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                profiles:
                  - id: child
                    name: Child
                    extends: nonexistent
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("extends unknown profile" in e for e in result.errors)

    def test_multi_layer_extends(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                profiles:
                  - id: grandparent
                    name: GP
                  - id: parent
                    name: Parent
                    extends: grandparent
                  - id: child
                    name: Child
                    extends: parent
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("multi-layer inheritance" in e for e in result.errors)

    def test_parent_with_mixins(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                mixins:
                  - id: mx
                    name: MX
                profiles:
                  - id: parent
                    name: Parent
                    mixins:
                      - mx
                  - id: child
                    name: Child
                    extends: parent
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("must not have 'mixins'" in e for e in result.errors)

    def test_unknown_mixin_reference(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                profiles:
                  - id: p1
                    name: P1
                    mixins:
                      - nonexistent
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("unknown mixin" in e for e in result.errors)

    def test_mixin_with_extends_is_error(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                mixins:
                  - id: bad-mixin
                    name: Bad
                    extends: something
                profiles:
                  - id: p1
                    name: P1
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("mixins must not have 'extends'" in e for e in result.errors)

    def test_mixin_with_mixins_is_error(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                mixins:
                  - id: bad-mixin
                    name: Bad
                    mixins:
                      - something
                profiles:
                  - id: p1
                    name: P1
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("mixins must not have 'mixins'" in e for e in result.errors)

    def test_valid_inheritance_no_errors(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                mixins:
                  - id: strict
                    name: Strict
                    behavior:
                      safety_level: conservative
                profiles:
                  - id: base
                    name: Base
                    behavior:
                      plan_first: false
                  - id: child
                    name: Child
                    extends: base
                    mixins:
                      - strict
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert result.errors == []


class TestDoctorAgents:
    """Doctor checks for agents.yaml validation."""

    def test_agent_missing_role(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: bad-agent
                    name: Bad
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("missing required 'role'" in e for e in result.errors)

    def test_agent_invalid_role(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: bad-agent
                    name: Bad
                    role: nonexistent-role
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("invalid role" in e for e in result.errors)

    def test_agent_unknown_profile_reference(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: bad-agent
                    name: Bad
                    role: dev
                    profile: nonexistent-profile
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("unknown profile" in e for e in result.errors)

    def test_agent_missing_knowledge_path_warning(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: test-agent
                    name: Test
                    role: dev
                    knowledge:
                      - nonexistent/path/
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("knowledge path not found" in w for w in result.warnings)

    def test_valid_agent_no_errors(self, tmp_root: Path):
        (tmp_root / "meta" / "profiles.yaml").write_text(
            textwrap.dedent("""\
                profiles:
                  - id: default-coding
                    name: Default
            """),
            encoding="utf-8",
        )
        kdir = tmp_root / "knowledge" / "standards"
        kdir.mkdir(parents=True)
        (kdir / "example.md").write_text("example", encoding="utf-8")
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: test-dev
                    name: Dev
                    role: dev
                    profile: default-coding
                    knowledge:
                      - knowledge/standards/
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert not any("test-dev" in e for e in result.errors)

    def test_worktree_missing_branch_pattern(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: dev
                    name: Dev
                    role: dev
                    worktree:
                      path_pattern: "repos/{repo_id}/.worktrees/{feature_id}"
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("branch_pattern" in e for e in result.errors)

    def test_worktree_missing_path_pattern(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: dev
                    name: Dev
                    role: dev
                    worktree:
                      branch_pattern: "feature/{feature_id}"
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("path_pattern" in e for e in result.errors)

    def test_worktree_on_non_dev_warns(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: pm
                    name: PM
                    role: pm
                    worktree:
                      branch_pattern: "feature/{feature_id}"
                      path_pattern: "repos/{repo_id}/.worktrees/{feature_id}"
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("intended for role=dev" in w for w in result.warnings)

    def test_worktree_placeholder_warnings(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: dev
                    name: Dev
                    role: dev
                    worktree:
                      branch_pattern: "feature/hardcoded"
                      path_pattern: "repos/hardcoded"
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("feature_id" in w for w in result.warnings)
        assert any("repo_id" in w for w in result.warnings)


class TestDoctorDeployYaml:
    """Doctor checks for deploy.yaml validation in repos."""

    def test_valid_deploy_yaml_no_errors(self, tmp_root: Path):
        # Set up sre agent
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: sre
                    name: SRE
                    role: sre
                    deploy_manifest: deploy.yaml
            """),
            encoding="utf-8",
        )
        # Create repo dir with valid deploy.yaml
        repo_dir = tmp_root / "repos" / "develop" / "alpha"
        repo_dir.mkdir(parents=True)
        (repo_dir / ".git").mkdir()
        (repo_dir / "deploy.yaml").write_text(
            textwrap.dedent("""\
                version: "1"
                name: alpha-svc
                strategy: manual
                stages:
                  - id: staging
                    cmd: "echo deploy"
                    health_check: "echo ok"
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert not any("deploy.yaml" in e for e in result.errors)

    def test_invalid_deploy_yaml_errors(self, tmp_root: Path):
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: sre
                    name: SRE
                    role: sre
                    deploy_manifest: deploy.yaml
            """),
            encoding="utf-8",
        )
        repo_dir = tmp_root / "repos" / "develop" / "alpha"
        repo_dir.mkdir(parents=True)
        (repo_dir / ".git").mkdir()
        (repo_dir / "deploy.yaml").write_text(
            textwrap.dedent("""\
                version: "2"
                name: ""
                strategy: invalid
                stages: []
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        deploy_errors = [e for e in result.errors if "deploy.yaml" in e]
        assert len(deploy_errors) >= 3  # version, name, strategy, stages

    def test_missing_deploy_yaml_no_error(self, tmp_root: Path):
        """Repos without deploy.yaml should not cause errors."""
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: sre
                    name: SRE
                    role: sre
                    deploy_manifest: deploy.yaml
            """),
            encoding="utf-8",
        )
        repo_dir = tmp_root / "repos" / "develop" / "alpha"
        repo_dir.mkdir(parents=True)
        (repo_dir / ".git").mkdir()
        result = doctor(tmp_root)
        assert not any("deploy.yaml" in e for e in result.errors)


class TestDoctorKnowledgeIntegrity:
    """Doctor checks for knowledge reference integrity."""

    def test_workspace_knowledge_path_not_found(self, tmp_root: Path):
        """Workspace referencing a non-existent knowledge path should warn."""
        (tmp_root / "meta" / "workspaces.yaml").write_text(
            textwrap.dedent("""\
                workspaces:
                  - id: ws1
                    context:
                      knowledge:
                        - knowledge/nonexistent/
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert any("knowledge path not found" in w and "ws1" in w for w in result.warnings)

    def test_unreferenced_knowledge_file_is_valid_catalog_content(self, tmp_root: Path):
        """Vault notes need not be eagerly injected into an agent/workspace."""
        # Create a knowledge file that no one references
        kdir = tmp_root / "knowledge" / "standards"
        kdir.mkdir(parents=True)
        (kdir / "referenced.md").write_text("referenced", encoding="utf-8")
        (kdir / "orphan.md").write_text("orphan", encoding="utf-8")

        # Agent only references one file
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: dev
                    name: Dev
                    role: dev
                    knowledge:
                      - knowledge/standards/referenced.md
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        assert not any(w.startswith("knowledge: orphan") for w in result.warnings)

    def test_directory_ref_covers_all_files(self, tmp_root: Path):
        """Directory reference like knowledge/ should cover all files under it."""
        kdir = tmp_root / "knowledge" / "standards"
        kdir.mkdir(parents=True)
        (kdir / "a.md").write_text("a", encoding="utf-8")
        (kdir / "b.md").write_text("b", encoding="utf-8")
        (kdir / "private.md").write_text(
            "---\nvisibility: private\n---\nprivate", encoding="utf-8",
        )

        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: keeper
                    name: Keeper
                    role: knowledge-keeper
                    knowledge:
                      - knowledge/
            """),
            encoding="utf-8",
        )
        result = doctor(tmp_root)
        # No orphans since knowledge/ covers everything
        assert not any(w.startswith("knowledge: orphan") for w in result.warnings)

    def test_no_orphan_warning_when_no_knowledge_dir(self, tmp_root: Path):
        """No orphan knowledge file warnings when knowledge/ directory doesn't exist."""
        result = doctor(tmp_root)
        # Use a more specific pattern to avoid false positives from temp dir paths
        # that may contain the word "orphan" in the test function name.
        orphan_knowledge_warnings = [
            w for w in result.warnings
            if w.startswith("knowledge: orphan")
        ]
        assert orphan_knowledge_warnings == []

    def test_at_reference_pattern(self):
        """AT_REF_PATTERN should match file refs and exclude email/CSS."""
        text = (
            "See @knowledge/standards/agent-framework.md for details.\n"
            "Also @docs/architecture.md and @features/README.md.\n"
            "Email: user@example.com should NOT match.\n"
            "CSS: @keyframes pulse should NOT match.\n"
        )
        matches = AT_REF_PATTERN.findall(text)
        assert matches == [
            "knowledge/standards/agent-framework.md",
            "docs/architecture.md",
            "features/README.md",
        ]

    def test_dangling_at_reference_warning(self, tmp_root: Path):
        """Dangling @-reference in a .md file should produce a warning."""
        kdir = tmp_root / "knowledge" / "standards"
        kdir.mkdir(parents=True)
        (kdir / "existing.md").write_text("exists", encoding="utf-8")
        # File with a dangling @-reference
        (kdir / "broken.md").write_text(
            "See @knowledge/standards/nonexistent.md for details.\n",
            encoding="utf-8",
        )

        # Ensure the agent references knowledge/ so no orphan warnings
        (tmp_root / "meta" / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: keeper
                    name: Keeper
                    role: knowledge-keeper
                    knowledge:
                      - knowledge/
            """),
            encoding="utf-8",
        )

        warnings: list[str] = []
        _check_at_references(tmp_root, warnings)
        assert any("dangling" in w and "nonexistent.md" in w for w in warnings)

    def test_valid_at_reference_no_warning(self, tmp_root: Path):
        """Valid @-reference should not produce a warning."""
        kdir = tmp_root / "knowledge" / "standards"
        kdir.mkdir(parents=True)
        (kdir / "target.md").write_text("target content", encoding="utf-8")
        (kdir / "source.md").write_text(
            "See @knowledge/standards/target.md for details.\n",
            encoding="utf-8",
        )

        warnings: list[str] = []
        _check_at_references(tmp_root, warnings)
        assert not any("target.md" in w for w in warnings)

    def test_private_note_dangling_reference_is_not_reported(self, tmp_root: Path):
        private_note = tmp_root / "knowledge" / "family" / "private.md"
        private_note.parent.mkdir(parents=True)
        private_note.write_text(
            "---\nvisibility: private\n---\n@knowledge/family/missing.md\n",
            encoding="utf-8",
        )

        warnings: list[str] = []
        _check_at_references(tmp_root, warnings)

        assert warnings == []

    def test_collect_all_knowledge_references(self):
        """_collect_all_knowledge_references collects from both workspaces and agents."""
        workspaces = [
            {
                "id": "ws1",
                "context": {
                    "knowledge": ["knowledge/standards/", "docs/architecture.md"],
                },
            }
        ]
        agents = [
            {
                "id": "dev",
                "knowledge": ["knowledge/standards/agent-framework.md", "meta/repos.yaml"],
            }
        ]
        refs = _collect_all_knowledge_references(workspaces, agents)
        assert "knowledge/standards/" in refs
        assert "docs/architecture.md" in refs
        assert "knowledge/standards/agent-framework.md" in refs
        assert "meta/repos.yaml" in refs

    def test_expand_directory_refs(self, tmp_root: Path):
        """_expand_directory_refs expands directories to individual .md files."""
        kdir = tmp_root / "knowledge" / "standards"
        kdir.mkdir(parents=True)
        (kdir / "a.md").write_text("a", encoding="utf-8")
        (kdir / "b.md").write_text("b", encoding="utf-8")
        # Non-markdown file should be excluded
        (kdir / "c.txt").write_text("c", encoding="utf-8")

        refs = {"knowledge/standards/", "docs/architecture.md"}
        expanded = _expand_directory_refs(tmp_root, refs)
        assert "knowledge/standards/a.md" in expanded
        assert "knowledge/standards/b.md" in expanded
        assert "knowledge/standards/private.md" not in expanded
        assert "knowledge/standards/c.txt" not in expanded
        # Non-directory ref passes through unchanged
        assert "docs/architecture.md" in expanded


class TestKnowledgeGraph:
    """Tests for generate_knowledge_graph."""

    def _setup_graph_root(self, tmp_path: Path) -> Path:
        """Create a minimal one-context root with knowledge files and agents."""
        meta = tmp_path / "meta"
        meta.mkdir()

        (meta / "repos.yaml").write_text(
            "repos:\n  - url: git@test.local:acme/alpha.git\n    category: develop\n",
            encoding="utf-8",
        )
        (meta / "workspaces.yaml").write_text(
            textwrap.dedent("""\
                workspaces:
                  - id: dev
                    name: Dev
                    context:
                      knowledge:
                        - knowledge/standards/
            """),
            encoding="utf-8",
        )
        (meta / "agents.yaml").write_text(
            textwrap.dedent("""\
                agents:
                  - id: dev
                    name: Dev
                    role: dev
                    knowledge:
                      - knowledge/standards/conventions.md
                  - id: pm
                    name: PM
                    role: pm
                    knowledge:
                      - knowledge/playbooks/add-feature.md
            """),
            encoding="utf-8",
        )

        # Create knowledge files
        kdir = tmp_path / "knowledge" / "standards"
        kdir.mkdir(parents=True)
        (kdir / "conventions.md").write_text("# Conventions\n", encoding="utf-8")
        (kdir / "testing.md").write_text("See @knowledge/standards/conventions.md\n", encoding="utf-8")

        pdir = tmp_path / "knowledge" / "playbooks"
        pdir.mkdir(parents=True)
        (pdir / "add-feature.md").write_text("# Add Feature\n", encoding="utf-8")

        return tmp_path

    def test_graph_contains_mermaid_block(self, tmp_path: Path):
        root = self._setup_graph_root(tmp_path)
        graph = generate_knowledge_graph(root)
        assert graph.startswith("```mermaid")
        assert graph.endswith("```")

    def test_graph_contains_agents(self, tmp_path: Path):
        root = self._setup_graph_root(tmp_path)
        graph = generate_knowledge_graph(root)
        assert "agent_dev" in graph
        assert "agent_pm" in graph

    def test_graph_contains_knowledge_nodes(self, tmp_path: Path):
        root = self._setup_graph_root(tmp_path)
        graph = generate_knowledge_graph(root)
        # conventions.md is referenced by dev agent
        assert "conventions" in graph
        # add-feature.md is referenced by pm agent
        assert "add_feature" in graph or "add-feature" in graph

    def test_graph_contains_edges(self, tmp_path: Path):
        root = self._setup_graph_root(tmp_path)
        graph = generate_knowledge_graph(root)
        # Agent → knowledge edges should be present
        assert "reads" in graph

    def test_graph_contains_workspace(self, tmp_path: Path):
        root = self._setup_graph_root(tmp_path)
        graph = generate_knowledge_graph(root)
        assert "ws_dev" in graph

    def test_graph_at_ref_edge(self, tmp_path: Path):
        root = self._setup_graph_root(tmp_path)
        graph = generate_knowledge_graph(root)
        # testing.md references conventions.md via @-ref
        assert "@ref" in graph
