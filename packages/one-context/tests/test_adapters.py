"""Tests for every independently registered tool adapter."""

from __future__ import annotations

import json
import importlib
import pkgutil
import textwrap
from pathlib import Path

import pytest

from one_context.adapters import (
    ADAPTERS,
    GeneratedFile,
    get_adapter,
    list_adapters,
    register,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def adapter_root(tmp_path: Path) -> Path:
    """Create a minimal one-context tree with knowledge files."""
    meta = tmp_path / "meta"
    meta.mkdir()

    (meta / "repos.yaml").write_text(
        textwrap.dedent("""\
            repos:
              - url: git@test.local:acme/alpha.git
                category: develop
        """),
        encoding="utf-8",
    )
    (meta / "profiles.yaml").write_text(
        textwrap.dedent("""\
            profiles:
              - id: default-coding
                name: Default Coding
                description: Balanced editing profile.
                mode: edit
                behavior:
                  plan_first: false
                  test_expectation: targeted
                  safety_level: standard
                  change_scope: focused
                context_policy:
                  load:
                    - repo-readme
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
                name: Development
                repos:
                  - alpha
                profiles:
                  - default-coding
                context:
                  summary: Development workspace for daily coding.
                  focus:
                    - feature development
                    - bug fixes
                  knowledge:
                    - knowledge/standards/
        """),
        encoding="utf-8",
    )
    (meta / "agents.yaml").write_text(
        textwrap.dedent("""\
            version: "1"
            agents:
              - id: pm
                name: PM Agent
                role: pm
                profile: default-coding
                description: Test PM agent.
                knowledge:
                  - knowledge/standards/
                owns:
                  - "features/**/spec.md"
                instructions: |
                  You are the PM agent for tests.
        """),
        encoding="utf-8",
    )

    # Create knowledge files
    knowledge_dir = tmp_path / "knowledge" / "standards"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "conventions.md").write_text(
        "# Conventions\n\nFollow these conventions.\n",
        encoding="utf-8",
    )

    # Project overview consumed by project_artifacts.
    refs = tmp_path / "knowledge" / "references"
    refs.mkdir(parents=True)
    (refs / "project-overview.md").write_text(
        "<!-- INTERNAL -->\n\n# Overview\n\n## What\nhello overview\n",
        encoding="utf-8",
    )
    # A skill so integration tests exercise the generate_skills →
    # generate_project_artifacts state-passing path.
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill.\ntriggers:\n  - demo\n---\nbody\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def dev_context(adapter_root: Path) -> dict:
    """Build context for the 'dev' workspace."""
    from one_context.context import build_workspace_context
    return build_workspace_context(adapter_root, "dev")


@pytest.fixture()
def minimal_root(tmp_path: Path) -> Path:
    """Create a one-context tree with output_style.tone: minimal (triggers top-placement)."""
    meta = tmp_path / "meta"
    meta.mkdir()

    (meta / "repos.yaml").write_text(
        textwrap.dedent("""\
            repos:
              - url: git@test.local:acme/alpha.git
                category: develop
        """),
        encoding="utf-8",
    )
    (meta / "profiles.yaml").write_text(
        textwrap.dedent("""\
            profiles:
              - id: default-coding
                name: Default Coding
                description: Minimal output profile.
                mode: edit
                behavior:
                  plan_first: false
                  test_expectation: targeted
                  safety_level: standard
                  change_scope: focused
                context_policy:
                  load:
                    - repo-readme
                output_style:
                  tone: minimal
                  plain_language: true
                  visuals: when_helpful
                  include_verification: true
        """),
        encoding="utf-8",
    )
    (meta / "workspaces.yaml").write_text(
        textwrap.dedent("""\
            workspaces:
              - id: dev
                name: Development
                repos:
                  - alpha
                profiles:
                  - default-coding
                context:
                  summary: Dev workspace with minimal tone.
                  knowledge: []
        """),
        encoding="utf-8",
    )
    (meta / "agents.yaml").write_text(
        textwrap.dedent("""\
            version: "1"
            agents:
              - id: dev
                name: Dev Agent
                role: dev
                profile: default-coding
                description: Dev agent with minimal tone.
                knowledge: []
                owns:
                  - "features/**/worktrees.yaml"
                instructions: |
                  You are the dev agent.
        """),
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def minimal_context(minimal_root: Path) -> dict:
    """Build context for the minimal-tone workspace."""
    from one_context.context import build_workspace_context
    return build_workspace_context(minimal_root, "dev")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_all_adapters_registered(self):
        assert set(list_adapters()) == {
            "claude_code",
            "codex",
            "cursor",
            "hermes",
            "openclaw",
            "opencode",
            "pi",
        }

    def test_one_public_module_per_adapter(self):
        """Keep tool adapters independent and out of the registry/CLI files."""
        import one_context.adapters as adapter_package

        public_modules = {
            info.name
            for info in pkgutil.iter_modules(adapter_package.__path__)
            if not info.ispkg and not info.name.startswith("_")
        }
        for module_name in public_modules:
            importlib.import_module(f"one_context.adapters.{module_name}")

        assert public_modules == set(ADAPTERS)
        for adapter_name, adapter_class in ADAPTERS.items():
            assert adapter_class.__module__ == (
                f"one_context.adapters.{adapter_name}"
            )

    def test_get_adapter(self):
        adapter = get_adapter("claude_code")
        assert adapter.name == "claude_code"

    def test_get_unknown_adapter(self):
        with pytest.raises(ValueError, match="Unknown adapter"):
            get_adapter("nonexistent")

    def test_duplicate_adapter_registration_is_rejected(self):
        list_adapters()
        duplicate = type(
            "DuplicatePiAdapter",
            (),
            {"__module__": "one_context.adapters.pi"},
        )
        with pytest.raises(ValueError, match="already registered"):
            register("pi")(duplicate)


# ---------------------------------------------------------------------------
# Claude Code Adapter
# ---------------------------------------------------------------------------

class TestClaudeCodeAdapter:
    def test_generates_file(self, adapter_root, dev_context):
        adapter = get_adapter("claude_code")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        assert len(files) == 1
        gf = files[0]
        assert isinstance(gf, GeneratedFile)
        assert gf.rel_path == ".claude/adapters/onecxt-dev.md"
        assert "dev" in gf.description

    def test_content_has_workspace_header(self, adapter_root, dev_context):
        adapter = get_adapter("claude_code")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert "# one-context" in content
        assert "`dev`" in content

    def test_content_has_profile_rules(self, adapter_root, dev_context):
        adapter = get_adapter("claude_code")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert "Profile: Default Coding" in content
        # Should have the "no plan" rule since plan_first=false
        assert "without" in content.lower() or "directly" in content.lower()

    def test_content_has_knowledge_refs(self, adapter_root, dev_context):
        adapter = get_adapter("claude_code")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        # Claude Code uses @file references
        assert "@knowledge/" in content

    def test_supports_file_ref(self):
        adapter = get_adapter("claude_code")
        assert adapter.supports_file_ref is True

    def test_content_has_focus_areas(self, adapter_root, dev_context):
        adapter = get_adapter("claude_code")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert "Focus Areas" in content
        assert "feature development" in content

    def test_generate_project_artifacts_writes_rules_only(self, adapter_root):
        from one_context.agents import load_agents

        rules_dir = adapter_root / ".onecxt"
        rules_dir.mkdir()
        rules_text = "# Project Rules\n\n1. Keep the canonical rules intact."
        (rules_dir / "RULES.md").write_text(rules_text, encoding="utf-8")

        adapter = get_adapter("claude_code")
        agents, _ = load_agents(adapter_root)
        files = adapter.generate_project_artifacts(adapter_root, ["dev"], agents)
        assert len(files) == 1
        assert files[0].rel_path == "CLAUDE.md"
        assert files[0].content.endswith(rules_text + "\n")
        assert "@.claude/adapters/onecxt-dev.md" not in files[0].content
        assert "@.claude/agents/pm.md" not in files[0].content
        assert files[0].source_path == ".onecxt/RULES.md"

    def test_agents_are_discovered_without_root_references(self, adapter_root):
        """Every agent gets a native stub; CLAUDE.md does not force-load any."""
        from one_context.agents import load_agents

        adapter = get_adapter("claude_code")
        agents, _ = load_agents(adapter_root)
        # Ensure the fixture has at least one on_demand agent to exercise the path.
        if not any(a.get("on_demand") for a in agents):
            agents = list(agents) + [
                {"id": "ghost", "role": "qa", "on_demand": True,
                 "instructions": "n/a", "owns": []}
            ]
        # Per-agent files are generated for both default and on-demand agents.
        per_agent = adapter.generate_agents(adapter_root, agents, {})
        per_agent_ids = {f.rel_path for f in per_agent}
        assert ".claude/agents/ghost.md" in per_agent_ids
        assert ".claude/agents/pm.md" in per_agent_ids

        files = adapter.generate_project_artifacts(adapter_root, ["dev"], agents)
        claude_md = next(f for f in files if f.rel_path == "CLAUDE.md").content
        assert "@.claude/agents/ghost.md" not in claude_md
        assert "@.claude/agents/pm.md" not in claude_md
        assert "auto-discovered" in files[0].limitations[0]

    def test_project_artifacts_ignore_overview_and_skill_routing(self, adapter_root):
        """Non-rule context stays in native .claude stubs, not root CLAUDE.md."""
        from one_context.agents import load_agents
        from one_context.skills import discover_skills

        # Canonical overview source (refs/ already created by adapter_root fixture).
        refs = adapter_root / "knowledge" / "references"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "project-overview.md").write_text(
            "<!-- INTERNAL -->\n\n# one-context — Project Overview\n\nintro\n\n"
            "## What This Repository Is\n\nan umbrella repository for tests.\n",
            encoding="utf-8",
        )
        # A skill to populate the routing table.
        skill_dir = adapter_root / "skills" / "demo"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill. Invocation phrase: demo please.\n---\nbody",
            encoding="utf-8",
        )
        rules_dir = adapter_root / ".onecxt"
        rules_dir.mkdir()
        (rules_dir / "RULES.md").write_text(
            "# Project Rules\n\nCanonical rule only.",
            encoding="utf-8",
        )

        adapter = get_adapter("claude_code")
        agents, _ = load_agents(adapter_root)
        skills = discover_skills(adapter_root)
        adapter.generate_skills(adapter_root, skills)  # populates adapter._skills
        files = adapter.generate_project_artifacts(adapter_root, ["dev"], agents)

        claude_md = next(f for f in files if f.rel_path == "CLAUDE.md").content
        assert "Canonical rule only." in claude_md
        assert "## What This Repository Is" not in claude_md
        assert "## Skill Routing (generated)" not in claude_md
        assert "`skills/demo/SKILL.md`" not in claude_md
        assert "Invocation phrase" not in claude_md


class TestAgentsMd:
    """Tests for the tool-neutral project-level AGENTS.md generator."""

    def _make_root(self, tmp_path: Path) -> Path:
        refs = tmp_path / "knowledge" / "references"
        refs.mkdir(parents=True)
        (refs / "project-overview.md").write_text(
            "<!-- INTERNAL -->\n\n# Project Overview\n\nintro line\n\n"
            "## Architecture\n\nsix layers.\n",
            encoding="utf-8",
        )
        rules_dir = tmp_path / ".onecxt"
        rules_dir.mkdir()
        (rules_dir / "RULES.md").write_text(
            "# Project Rules\n\n1. Preserve user changes.\n",
            encoding="utf-8",
        )
        skill_dir = tmp_path / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo skill.\ntriggers:\n  - demo\n---\nbody",
            encoding="utf-8",
        )
        return tmp_path

    def test_build_agents_md(self, tmp_path):
        from one_context.project_artifacts import build_agents_md
        root = self._make_root(tmp_path)
        content = build_agents_md(root)

        assert content.startswith("<!--")  # generated header
        assert "GENERATED by onecxt adapt" in content
        assert "# AGENTS.md" in content
        # Overview inlined (title dropped, starts at first H2).
        assert "## Architecture" in content
        assert "six layers." in content
        assert "## Project Rules" in content
        assert "Preserve user changes." in content
        # Skills are discovered from native stubs; AGENTS.md has no second-hand
        # routing policy or generated routing table that could drift.
        assert "no routing table is inlined here" in content
        assert "## Skill Routing (generated)" not in content
        assert "`skills/demo/SKILL.md`" not in content

    def test_no_at_references(self, tmp_path):
        """AGENTS.md must inline everything — Cursor/Codex don't follow @-refs."""
        from one_context.project_artifacts import build_agents_md
        root = self._make_root(tmp_path)
        content = build_agents_md(root)
        for line in content.splitlines():
            assert not line.strip().startswith("@"), f"unexpected @-ref: {line}"


class TestClaudeCodeRootRules:
    """Root CLAUDE.md remains a direct copy of the canonical rules source."""

    @staticmethod
    def _write_rules(root: Path) -> str:
        rules_dir = root / ".onecxt"
        rules_dir.mkdir()
        rules_text = "# Project Rules\n\n1. Canonical rules stay authoritative."
        (rules_dir / "RULES.md").write_text(rules_text, encoding="utf-8")
        return rules_text

    def test_derived_profile_rules_are_not_added_to_root(
        self, minimal_root, minimal_context,
    ):
        from one_context.agents import load_agents

        rules_text = self._write_rules(minimal_root)
        adapter = get_adapter("claude_code")
        ws = minimal_context["workspace"]
        adapter.generate(minimal_root, ws, minimal_context)
        agents, _ = load_agents(minimal_root)
        adapter.generate_agents(minimal_root, agents, {})
        files = adapter.generate_project_artifacts(minimal_root, ["dev"], agents)

        claude_md = next(f for f in files if f.rel_path == "CLAUDE.md")
        assert claude_md.content.endswith(rules_text + "\n")
        assert "ALWAYS respond" not in claude_md.content

    def test_no_derived_hard_rules_file_is_generated(
        self, minimal_root, minimal_context,
    ):
        from one_context.agents import load_agents

        self._write_rules(minimal_root)
        adapter = get_adapter("claude_code")
        ws = minimal_context["workspace"]
        adapter.generate(minimal_root, ws, minimal_context)

        agents, _ = load_agents(minimal_root)
        adapter.generate_agents(minimal_root, agents, {})
        files = adapter.generate_project_artifacts(minimal_root, ["dev"], agents)

        assert [f.rel_path for f in files] == ["CLAUDE.md"]

    def test_root_rules_have_no_generated_references(
        self, minimal_root, minimal_context,
    ):
        from one_context.agents import load_agents

        self._write_rules(minimal_root)
        adapter = get_adapter("claude_code")
        ws = minimal_context["workspace"]
        adapter.generate(minimal_root, ws, minimal_context)

        agents, _ = load_agents(minimal_root)
        adapter.generate_agents(minimal_root, agents, {})
        files = adapter.generate_project_artifacts(minimal_root, ["dev"], agents)

        claude_md = next(f for f in files if f.rel_path == "CLAUDE.md")
        assert not any(
            line.strip().startswith("@")
            for line in claude_md.content.splitlines()
        )

    def test_inline_rules_exclude_top_placement(self, minimal_root, minimal_context):
        adapter = get_adapter("claude_code")
        ws = minimal_context["workspace"]
        files = adapter.generate(minimal_root, ws, minimal_context)

        content = files[0].content
        # The soft "Default to minimal output" should NOT appear in the inline
        # workspace file (it was promoted to top-placement)
        assert "Default to minimal output" not in content


# ---------------------------------------------------------------------------
# Hermes Adapter
# ---------------------------------------------------------------------------

class TestHermesAdapter:
    def test_generates_file(self, adapter_root, dev_context):
        adapter = get_adapter("hermes")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        assert len(files) == 1
        gf = files[0]
        assert isinstance(gf, GeneratedFile)
        assert gf.rel_path == ".hermes/onecxt-dev.md"

    def test_content_has_workspace_header(self, adapter_root, dev_context):
        adapter = get_adapter("hermes")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert "# one-context" in content
        assert "`dev`" in content

    def test_content_has_profile_rules(self, adapter_root, dev_context):
        adapter = get_adapter("hermes")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert "Profile: Default Coding" in content
        assert "without" in content.lower() or "directly" in content.lower()

    def test_content_inlines_knowledge(self, adapter_root, dev_context):
        adapter = get_adapter("hermes")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        # Hermes inlines knowledge content
        assert "Conventions" in content
        assert "<!-- source:" in content

    def test_does_not_support_file_ref(self):
        adapter = get_adapter("hermes")
        assert adapter.supports_file_ref is False

    def test_content_has_focus_areas(self, adapter_root, dev_context):
        adapter = get_adapter("hermes")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert "Focus Areas" in content
        assert "feature development" in content

    def test_skill_resources_are_rebased_for_hermes_skill_file(self, adapter_root):
        from one_context.skills import discover_skills

        skill_dir = adapter_root / "skills" / "demo"
        resources = {
            "references/guide.md": "# Guide\n",
            "assets/diagram.png": "not-a-real-png",
            "scripts/run.py": "print('ok')\n",
            "templates/example.txt": "example\n",
        }
        for relative_path, content in resources.items():
            resource = skill_dir / relative_path
            resource.parent.mkdir(parents=True, exist_ok=True)
            resource.write_text(content, encoding="utf-8")

        (skill_dir / "SKILL.md").write_text(
            textwrap.dedent("""\
                ---
                name: demo
                description: Demo skill.
                ---
                Read [the guide](references/guide.md#intro "Guide").
                View ![diagram](assets/diagram.png).
                [runner]: <scripts/run.py>
                Use `templates/example.txt` and the `references/` directory.
                Keep [external](https://example.com/guide), [anchor](#local),
                [absolute](/tmp/guide.md), [canonical](skills/demo/references/guide.md),
                `$SKILL_ROOT/references/guide.md`, and `features/demo/spec.md` unchanged.
            """),
            encoding="utf-8",
        )

        adapter = get_adapter("hermes")
        skill_files = adapter.generate_skills(adapter_root, discover_skills(adapter_root))
        per_skill = next(f for f in skill_files if f.source_id == "demo")

        assert "(../../skills/demo/references/guide.md#intro \"Guide\")" in per_skill.content
        assert "(../../skills/demo/assets/diagram.png)" in per_skill.content
        assert "[runner]: <../../skills/demo/scripts/run.py>" in per_skill.content
        assert "`../../skills/demo/templates/example.txt`" in per_skill.content
        assert "`../../skills/demo/references/`" in per_skill.content

        unchanged = (
            "https://example.com/guide",
            "(#local)",
            "(/tmp/guide.md)",
            "(skills/demo/references/guide.md)",
            "`$SKILL_ROOT/references/guide.md`",
            "`features/demo/spec.md`",
        )
        for reference in unchanged:
            assert reference in per_skill.content

        canonical_guide = skill_dir / "references" / "guide.md"
        per_skill_target = (
            adapter_root
            / Path(per_skill.rel_path).parent
            / "../../skills/demo/references/guide.md"
        ).resolve()
        assert per_skill_target == canonical_guide.resolve()

    def test_generate_project_artifacts_writes_hermes_md(self, adapter_root):
        from one_context.agents import load_agents

        adapter = get_adapter("hermes")
        ws = self._get_dev_workspace(adapter_root)
        ctx = self._get_dev_context(adapter_root)
        adapter.generate(adapter_root, ws, ctx)

        agents, _ = load_agents(adapter_root)
        adapter.generate_agents(adapter_root, agents, {})
        files = adapter.generate_project_artifacts(adapter_root, ["dev"], agents)

        hermes_md = next(f for f in files if f.rel_path == ".hermes.md")
        assert hermes_md is not None
        assert ".hermes/onecxt-dev.md" in hermes_md.content
        assert ".hermes/agents/pm.md" in hermes_md.content
        assert "PM Agent" in hermes_md.content

    def test_root_routes_on_demand_agent_without_inlining_it(self, adapter_root):
        adapter = get_adapter("hermes")
        agents = [{
            "id": "qa",
            "name": "QA Agent",
            "on_demand": True,
            "instructions": "PRIVATE ON-DEMAND INSTRUCTIONS",
        }]
        adapter.generate_agents(adapter_root, agents, {})

        hermes_md = adapter.generate_project_artifacts(
            adapter_root, ["dev"], agents,
        )[0]

        assert ".hermes/agents/qa.md" in hermes_md.content
        assert "PRIVATE ON-DEMAND INSTRUCTIONS" not in hermes_md.content

    def test_root_context_stays_within_hermes_budget(self, adapter_root):
        from one_context.adapters.hermes import HERMES_ROOT_CHAR_BUDGET
        from one_context.agents import load_agents
        from one_context.skills import discover_skills

        adapter = get_adapter("hermes")
        agents, _ = load_agents(adapter_root)
        adapter.generate_skills(adapter_root, discover_skills(adapter_root))
        content = adapter.generate_project_artifacts(
            adapter_root, ["dev"], agents,
        )[0].content

        assert len(content) <= HERMES_ROOT_CHAR_BUDGET

    def test_root_context_rejects_content_over_hermes_budget(self, adapter_root):
        from one_context.adapters.hermes import HERMES_ROOT_CHAR_BUDGET

        adapter = get_adapter("hermes")
        adapter._top_rules = ["x" * (HERMES_ROOT_CHAR_BUDGET + 1)]

        with pytest.raises(ValueError, match="20,000-character budget"):
            adapter.generate_project_artifacts(adapter_root, [], [])

    @staticmethod
    def _get_dev_workspace(root: Path) -> dict:
        from one_context.context import build_workspace_context
        ctx = build_workspace_context(root, "dev")
        return ctx["workspace"]

    @staticmethod
    def _get_dev_context(root: Path) -> dict:
        from one_context.context import build_workspace_context
        return build_workspace_context(root, "dev")


class TestHermesTopPlacement:
    """Tests for top-placement (hard rules) when tone=minimal."""

    def test_hard_rules_prepended_to_hermes_md(self, minimal_root, minimal_context):
        from one_context.agents import load_agents

        adapter = get_adapter("hermes")
        ws = minimal_context["workspace"]
        adapter.generate(minimal_root, ws, minimal_context)

        agents, _ = load_agents(minimal_root)
        adapter.generate_agents(minimal_root, agents, {})
        files = adapter.generate_project_artifacts(minimal_root, ["dev"], agents)

        hermes_md = next(f for f in files if f.rel_path == ".hermes.md")
        assert "include only the information needed" in hermes_md.content
        # Hard rule should come before the title
        title_pos = hermes_md.content.index("# one-context")
        hard_pos = hermes_md.content.index("include only the information needed")
        assert hard_pos < title_pos

    def test_inline_rules_exclude_top_placement(self, minimal_root, minimal_context):
        adapter = get_adapter("hermes")
        ws = minimal_context["workspace"]
        files = adapter.generate(minimal_root, ws, minimal_context)
        content = files[0].content
        assert "Default to minimal output" not in content


class TestHermesGenerateAgents:
    def test_generates_agent_file(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("hermes")
        files = adapter.generate_agents(root, agents, profiles)
        assert len(files) >= 1
        assert any("pm.md" in gf.rel_path for gf in files)

    def test_content_has_instructions(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("hermes")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "PM agent" in content or "PM Agent" in content

    def test_content_has_artifact_ownership(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("hermes")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "Artifact Ownership" in content

    def test_content_has_profile_section(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("hermes")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "Profile" in content

    def test_inlines_knowledge(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("hermes")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "Conventions" in content


# ---------------------------------------------------------------------------
# Cursor Adapter
# ---------------------------------------------------------------------------

class TestCursorAdapter:
    def test_generates_mdc_file(self, adapter_root, dev_context):
        adapter = get_adapter("cursor")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        assert len(files) == 1
        gf = files[0]
        assert gf.rel_path == ".cursor/rules/onecxt-dev.mdc"

    def test_has_mdc_frontmatter(self, adapter_root, dev_context):
        adapter = get_adapter("cursor")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert content.startswith("---\n")
        assert "alwaysApply: true" in content

    def test_content_has_profile_rules(self, adapter_root, dev_context):
        adapter = get_adapter("cursor")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        assert "Profile:" in content

    def test_inlines_knowledge(self, adapter_root, dev_context):
        adapter = get_adapter("cursor")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        # Cursor inlines knowledge, should contain actual content
        assert "Conventions" in content
        assert "<!-- source:" in content

    def test_does_not_support_file_ref(self):
        adapter = get_adapter("cursor")
        assert adapter.supports_file_ref is False


class TestCursorTopPlacement:
    """Tests for top-placement (hard rules) when tone=minimal."""

    def test_hard_rules_mdc_generated(self, minimal_root, minimal_context):
        from one_context.agents import load_agents

        adapter = get_adapter("cursor")
        ws = minimal_context["workspace"]
        adapter.generate(minimal_root, ws, minimal_context)

        agents, _ = load_agents(minimal_root)
        files = adapter.generate_agents(minimal_root, agents, {})

        hard_rules = next(
            (f for f in files if f.rel_path == ".cursor/rules/onecxt-hard-rules.mdc"),
            None,
        )
        assert hard_rules is not None
        assert "alwaysApply: true" in hard_rules.content
        assert "include only the information needed" in hard_rules.content

    def test_inline_rules_exclude_top_placement(self, minimal_root, minimal_context):
        adapter = get_adapter("cursor")
        ws = minimal_context["workspace"]
        files = adapter.generate(minimal_root, ws, minimal_context)

        workspace_mdc = next(
            f for f in files if f.rel_path == ".cursor/rules/onecxt-dev.mdc"
        )
        # The soft "Default to minimal output" should NOT appear inline
        assert "Default to minimal output" not in workspace_mdc.content


# ---------------------------------------------------------------------------
# OpenClaw Adapter
# ---------------------------------------------------------------------------

class TestOpenClawAdapter:
    def test_generates_json_file(self, adapter_root, dev_context):
        adapter = get_adapter("openclaw")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        assert len(files) == 1
        gf = files[0]
        assert gf.rel_path == ".openclaw/onecxt-dev.json"

    def test_valid_json(self, adapter_root, dev_context):
        adapter = get_adapter("openclaw")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content

        data = json.loads(content)
        assert data["workspace_id"] == "dev"

    def test_has_instructions(self, adapter_root, dev_context):
        adapter = get_adapter("openclaw")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        data = json.loads(files[0].content)
        assert "instructions" in data
        assert len(data["instructions"]) > 0

    def test_has_knowledge(self, adapter_root, dev_context):
        adapter = get_adapter("openclaw")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        data = json.loads(files[0].content)
        assert "knowledge" in data
        assert len(data["knowledge"]) > 0
        assert "source" in data["knowledge"][0]
        assert "content" in data["knowledge"][0]

    def test_does_not_support_file_ref(self):
        adapter = get_adapter("openclaw")
        assert adapter.supports_file_ref is False

    def test_has_profile_metadata(self, adapter_root, dev_context):
        adapter = get_adapter("openclaw")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        data = json.loads(files[0].content)
        assert "profiles" in data
        assert data["profiles"][0]["id"] == "default-coding"

    def test_generate_project_artifacts_manifest(self, adapter_root):
        from one_context.agents import load_agents

        adapter = get_adapter("openclaw")
        agents, _ = load_agents(adapter_root)
        files = adapter.generate_project_artifacts(adapter_root, ["dev"], agents)
        assert len(files) == 1
        assert files[0].rel_path == ".openclaw/onecxt-project.json"
        data = json.loads(files[0].content)
        oc = data["one_context"]
        assert oc["version"] == 1
        assert oc["workspaces"][0]["path"] == ".openclaw/onecxt-dev.json"
        assert "agents" not in oc
        assert "skills" not in oc


# ---------------------------------------------------------------------------
# CLI adapt command (integration)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OpenCode Adapter
# ---------------------------------------------------------------------------

class TestOpenCodeAdapter:
    def test_generates_instruction_file(self, adapter_root, dev_context):
        adapter = get_adapter("opencode")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)

        assert len(files) == 1
        gf = files[0]
        assert gf.rel_path == ".opencode/instructions/onecxt-dev.md"
        assert "dev" in gf.description

    def test_workspace_has_marker(self, adapter_root, dev_context):
        adapter = get_adapter("opencode")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content
        assert "GENERATED by onecxt adapt" in content

    def test_workspace_header_and_summary(self, adapter_root, dev_context):
        adapter = get_adapter("opencode")
        ws = dev_context["workspace"]
        content = adapter.generate(adapter_root, ws, dev_context)[0].content
        assert "# one-context" in content
        assert "`dev`" in content
        assert "Development workspace for daily coding." in content

    def test_workspace_has_unified_rules(self, adapter_root, dev_context):
        adapter = get_adapter("opencode")
        ws = dev_context["workspace"]
        content = adapter.generate(adapter_root, ws, dev_context)[0].content
        assert "You may edit code directly without creating a formal plan first." in content
        assert "Write targeted tests for the specific changes you make." in content

    def test_workspace_inlines_knowledge(self, adapter_root, dev_context):
        """OpenCode has no @file ref — knowledge must be inlined as markdown."""
        adapter = get_adapter("opencode")
        ws = dev_context["workspace"]
        content = adapter.generate(adapter_root, ws, dev_context)[0].content
        assert "## Knowledge" in content
        assert "### file: `knowledge/standards/conventions.md`" in content
        assert "Follow these conventions." in content

    def test_does_not_support_file_ref(self):
        assert get_adapter("opencode").supports_file_ref is False

    def test_generate_agents_writes_md_with_frontmatter(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("opencode")
        files = adapter.generate_agents(root, agents, profiles)
        assert any("agent/pm.md" in gf.rel_path for gf in files)
        pm = next(f for f in files if f.rel_path == ".opencode/agent/pm.md")
        # YAML frontmatter must lead the file.
        assert pm.content.startswith("---\n")
        import yaml
        fm_end = pm.content.index("\n---\n", 4)
        fm = yaml.safe_load(pm.content[4:fm_end])
        assert fm["mode"] == "subagent"
        assert "description" in fm
        # Body carries instructions + inlined knowledge.
        body = pm.content[fm_end + 5:]
        assert "PM agent" in body or "PM Agent" in body
        assert "Conventions" in body

    def test_generate_skills_references_canonical_body(self, adapter_root):
        from one_context.skills import discover_skills

        adapter = get_adapter("opencode")
        skills = discover_skills(adapter_root)
        files = adapter.generate_skills(adapter_root, skills)
        assert len(files) == 1
        gf = files[0]
        assert gf.rel_path == ".opencode/skills/demo/SKILL.md"
        assert "@skills/demo/SKILL.md" in gf.content
        assert "canonical-source: skills/demo/SKILL.md" in gf.content
        assert "body" not in gf.content

    def test_generate_project_artifacts_writes_opencode_json(self, adapter_root):
        from one_context.agents import load_agents

        adapter = get_adapter("opencode")
        adapter._workspace_ids = ["dev"]  # simulate generate() having run
        agents, _ = load_agents(adapter_root)
        files = adapter.generate_project_artifacts(adapter_root, ["dev"], agents)
        assert len(files) == 1
        assert files[0].rel_path == ".opencode/opencode.json"
        data = json.loads(files[0].content)
        assert data["$schema"] == "https://opencode.ai/config.json"
        assert data["instructions"] == [".opencode/instructions/onecxt-dev.md"]
        assert data["skills"]["paths"] == [".opencode/skills"]

    def test_project_config_has_no_unknown_keys(self, adapter_root):
        """OpenCode rejects unknown top-level keys — only schema keys allowed."""
        from one_context.agents import load_agents

        adapter = get_adapter("opencode")
        agents, _ = load_agents(adapter_root)
        data = json.loads(
            adapter.generate_project_artifacts(adapter_root, ["dev"], agents)[0].content
        )
        valid = {"$schema", "instructions", "skills"}
        assert set(data.keys()).issubset(valid)


class TestPiAdapter:
    def test_aggregates_workspace_into_append_system(self, adapter_root, dev_context):
        adapter = get_adapter("pi")
        workspace = dev_context["workspace"]

        assert adapter.generate(adapter_root, workspace, dev_context) == []
        files = adapter.generate_project_artifacts(adapter_root, ["dev"], [])

        assert len(files) == 1
        generated = files[0]
        assert generated.rel_path == ".pi/APPEND_SYSTEM.md"
        assert generated.status == "aggregate"
        assert "GENERATED by onecxt adapt" in generated.content
        assert "Workspace: `dev`" in generated.content
        assert "Development workspace for daily coding." in generated.content
        assert "You may edit code directly" in generated.content
        assert "knowledge/standards" in generated.content

    def test_aggregate_contains_each_selected_workspace_once(
        self, adapter_root, dev_context
    ):
        adapter = get_adapter("pi")
        adapter.generate(
            adapter_root,
            dev_context["workspace"],
            dev_context,
        )
        second_workspace = {
            "id": "research",
            "context": {
                "summary": "Research workspace.",
                "focus": ["experiments"],
            },
        }
        adapter.generate(
            adapter_root,
            second_workspace,
            {**dev_context, "workspace": second_workspace},
        )

        content = adapter.generate_project_artifacts(
            adapter_root, ["dev", "research"], []
        )[0].content
        assert content.count("## Workspace: `dev`") == 1
        assert content.count("## Workspace: `research`") == 1

    def test_append_system_does_not_replace_pi_default(self, adapter_root, dev_context):
        adapter = get_adapter("pi")
        adapter.generate(adapter_root, dev_context["workspace"], dev_context)
        paths = {
            item.rel_path
            for item in adapter.generate_project_artifacts(adapter_root, ["dev"], [])
        }
        assert ".pi/APPEND_SYSTEM.md" in paths
        assert ".pi/SYSTEM.md" not in paths
        assert ".pi/settings.json" not in paths

    def test_generates_agent_prompt_translation(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("pi")
        files = adapter.generate_agents(root, agents, profiles)

        prompt = next(f for f in files if f.rel_path == ".pi/prompts/agent-pm.md")
        assert prompt.status == "translate"
        assert prompt.target_kind == "prompt"
        assert prompt.limitations
        assert "/agent-pm" in prompt.trigger
        assert prompt.content.startswith("---\n")
        assert "argument-hint: '[task]'" in prompt.content
        assert "PM agent" in prompt.content or "PM Agent" in prompt.content
        assert "Artifact Ownership" in prompt.content
        assert "${ARGUMENTS:-" in prompt.content
        assert "knowledge/standards" in prompt.content

    def test_agent_prompt_preserves_literal_pi_argument_tokens(
        self, agents_and_profiles
    ):
        from one_context.adapters.pi import _escape_pi_template_literals

        root, agents, profiles = agents_and_profiles
        agent = {
            **agents[0],
            "id": "dollar-agent",
            "instructions": "Run tool $1; keep $ARGUMENTS and ${VAR} literal.",
        }
        prompt = get_adapter("pi").generate_agents(
            root,
            [agent],
            profiles,
        )[0].content

        assert _escape_pi_template_literals("$1 $ARGUMENTS ${VAR}") == (
            "${0:-$}1 ${0:-$}ARGUMENTS ${0:-$}{VAR}"
        )
        assert "tool ${0:-$}1" in prompt
        assert "${0:-$}ARGUMENTS" in prompt
        assert "${0:-$}{VAR}" in prompt
        assert prompt.count("${ARGUMENTS:-") == 1

    def test_shared_agent_skill_is_native_for_pi_and_codex(self, adapter_root):
        from one_context.adapters._agent_skills import generate_shared_agent_skills
        from one_context.skills import discover_skills

        files = generate_shared_agent_skills(
            adapter_root,
            discover_skills(adapter_root),
        )
        generated = files[0]
        assert generated.rel_path == ".agents/skills/demo/SKILL.md"
        assert generated.status == "native"
        assert "description: Demo skill." in generated.content
        assert "@skills/demo/SKILL.md" in generated.content
        assert generated.source_digest

    @pytest.mark.parametrize("consumer", ["claude", "codex,pi", "pi"])
    def test_skill_surface_filter_preserves_native_registration(self, adapter_root, consumer):
        import yaml

        from one_context.adapters._agent_skills import generate_shared_agent_skills
        from one_context.skills import discover_skills

        skill = discover_skills(adapter_root)[0]
        skill.frontmatter["metadata"] = {"owner": "demo"}
        skill.frontmatter["aliases"] = ["demo"]
        original = (adapter_root / skill.source_path).read_bytes()
        if consumer == "claude":
            generated = get_adapter("claude_code").generate_skills(adapter_root, [skill])[0]
        else:
            generated = generate_shared_agent_skills(
                adapter_root, [skill], consumers=tuple(consumer.split(",")),
            )[0]
        frontmatter = yaml.safe_load(generated.content.split("---", 2)[1])
        assert frontmatter["metadata"] == {"owner": "demo", "surfaces": consumer}
        assert frontmatter["name"] == skill.name
        assert frontmatter["description"] == skill.frontmatter["description"]
        assert frontmatter["aliases"] == ["demo"]
        assert not frontmatter.get("disable-model-invocation", False)
        assert f"@{skill.source_path}" in generated.content
        assert (adapter_root / skill.source_path).read_bytes() == original

    def test_does_not_support_parser_level_file_refs(self):
        assert get_adapter("pi").supports_file_ref is False

    def test_skill_aliases_become_pi_short_name_prompts(self, adapter_root):
        from one_context.skills import discover_skills, SkillMeta

        skills = discover_skills(adapter_root)
        demo = next(s for s in skills if s.dir_name == "demo")
        demo.frontmatter["aliases"] = ["demo", "dm"]

        files = get_adapter("pi").generate_skills(adapter_root, skills)
        rels = {f.rel_path for f in files}
        assert rels == {".pi/prompts/demo.md", ".pi/prompts/dm.md"}

        sc_file = next(f for f in files if f.rel_path == ".pi/prompts/demo.md")
        assert sc_file.status == "translate"
        assert sc_file.target_kind == "prompt"
        assert sc_file.trigger.startswith("/demo ")
        assert "GENERATED by onecxt adapt" in sc_file.content
        assert "canonical-source: skills/demo/SKILL.md; alias: demo" in sc_file.content
        assert "/skill:demo" in sc_file.content
        assert "skills/demo/SKILL.md" in sc_file.content
        assert "${ARGUMENTS:-}" in sc_file.content
        assert sc_file.content.startswith("---\n")
        assert "argument-hint: '[args]'" in sc_file.content

    def test_skill_without_aliases_emits_no_pi_prompts(self, adapter_root):
        from one_context.skills import discover_skills

        skills = discover_skills(adapter_root)
        for skill in skills:
            skill.frontmatter.pop("aliases", None)

        files = get_adapter("pi").generate_skills(adapter_root, skills)
        assert files == []


class TestAdaptCLI:
    def test_duplicate_generated_paths_are_detected(self):
        from one_context.cli._adapt import _duplicate_generated_paths

        generated = [
            GeneratedFile("shared.md", "one", "first"),
            GeneratedFile("unique.md", "two", "second"),
            GeneratedFile("shared.md", "three", "third"),
        ]
        assert _duplicate_generated_paths(generated) == ["shared.md"]

    def test_unsafe_generated_paths_are_detected(self, tmp_path):
        from one_context.cli._adapt import _unsafe_generated_paths

        generated = [
            GeneratedFile("safe/file.md", "one", "safe"),
            GeneratedFile("../escape.md", "two", "traversal"),
            GeneratedFile("/absolute.md", "three", "absolute"),
        ]
        assert _unsafe_generated_paths(tmp_path, generated) == [
            "../escape.md",
            "/absolute.md",
        ]

    def test_adapt_dry_run(self, adapter_root, capsys):
        from one_context.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev", "--dry-run"])

        from one_context.cli import _cmd_adapt
        rc = _cmd_adapt(adapter_root, args)
        assert rc == 0

        captured = capsys.readouterr()
        assert "---" in captured.out  # dry-run separators
        assert "claude_code" in captured.out.lower() or "Claude Code" in captured.out

    def test_adapt_writes_files(self, adapter_root):
        from one_context.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev"])

        from one_context.cli import _cmd_adapt
        rc = _cmd_adapt(adapter_root, args)
        assert rc == 0

        # Check files were created
        assert (adapter_root / ".claude" / "adapters" / "onecxt-dev.md").is_file()
        assert not (adapter_root / ".cursor" / "rules" / "onecxt-dev.mdc").exists()
        assert (adapter_root / ".openclaw" / "onecxt-dev.json").is_file()
        assert (adapter_root / "CLAUDE.md").is_file()
        claude_md = (adapter_root / "CLAUDE.md").read_text(encoding="utf-8")
        assert "GENERATED by onecxt adapt" in claude_md
        assert "@.claude/adapters/onecxt-dev.md" not in claude_md
        assert (adapter_root / ".openclaw" / "onecxt-project.json").is_file()
        assert (adapter_root / ".claude" / "agents" / "pm.md").is_file()
        assert not (adapter_root / ".openclaw" / "agents" / "pm.json").exists()
        assert (adapter_root / ".pi" / "APPEND_SYSTEM.md").is_file()
        assert (adapter_root / ".pi" / "prompts" / "agent-pm.md").is_file()

        # Native skill stubs exist for supported project-level surfaces.
        assert (adapter_root / ".claude" / "skills" / "demo" / "SKILL.md").is_file()
        assert not (adapter_root / ".cursor" / "rules" / "skill-demo.mdc").exists()
        assert (adapter_root / ".hermes" / "skills" / "demo.md").is_file()
        assert not (adapter_root / ".openclaw" / "skills" / "demo.json").exists()
        assert (adapter_root / ".agents" / "skills" / "demo" / "SKILL.md").is_file()

        # Claude discovers native stubs and keeps root CLAUDE.md rules-only.
        claude_skill = (
            adapter_root / ".claude" / "skills" / "demo" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "@skills/demo/SKILL.md" in claude_skill
        assert "## Skills" not in claude_md
        assert "## Skill Routing (generated)" not in claude_md

        hermes_md = (adapter_root / ".hermes.md").read_text(encoding="utf-8")
        assert ".hermes/skills/demo.md" in hermes_md

        import json as _json
        proj = _json.loads(
            (adapter_root / ".openclaw" / "onecxt-project.json").read_text("utf-8")
        )
        assert "skills" not in proj["one_context"]
        assert "agents" not in proj["one_context"]

        agents_md = (adapter_root / "AGENTS.md").read_text(encoding="utf-8")
        assert "# AGENTS.md" in agents_md
        assert "## Skill Routing (generated)" not in agents_md

        report = _json.loads(
            (adapter_root / "adapt-report.json").read_text(encoding="utf-8")
        )
        paths = [entry["path"] for entry in report["entries"] if entry["path"]]
        assert len(paths) == len(set(paths)), "generated paths must have one owner"
        assert paths.count(".agents/skills/demo/SKILL.md") == 1
        by_path = {
            entry["path"]: entry
            for entry in report["entries"]
            if entry["path"]
        }
        assert by_path[".agents/skills/demo/SKILL.md"]["targets"] == [
            "codex",
            "pi",
        ]
        assert by_path["AGENTS.md"]["targets"] == [
            "codex",
            "opencode",
            "pi",
        ]

    def test_adapt_only_filter(self, adapter_root):
        from one_context.cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root), "adapt", "dev", "--only", "cursor",
        ])

        from one_context.cli import _cmd_adapt
        rc = _cmd_adapt(adapter_root, args)
        assert rc == 0

        assert (adapter_root / ".cursor" / "rules" / "onecxt-dev.mdc").is_file()
        assert not (adapter_root / ".claude" / "adapters" / "onecxt-dev.md").is_file()

    def test_adapt_prunes_skipped_cursor_rules(self, adapter_root):
        from one_context.adapters._shared_rules import GENERATED_HEADER_MD
        from one_context.cli import _cmd_adapt, build_parser

        rules = adapter_root / ".cursor" / "rules"
        rules.mkdir(parents=True)
        stale = rules / "skill-demo.mdc"
        stale.write_text(
            "---\nalwaysApply: true\n---\n" + GENERATED_HEADER_MD + "\n",
            encoding="utf-8",
        )
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev"])
        assert _cmd_adapt(adapter_root, args) == 0
        assert not stale.exists()

    def test_adapt_only_pi(self, adapter_root):
        from one_context.cli import build_parser, _cmd_adapt

        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root), "adapt", "dev", "--only", "pi",
        ])
        assert _cmd_adapt(adapter_root, args) == 0

        assert (adapter_root / ".pi" / "APPEND_SYSTEM.md").is_file()
        assert (adapter_root / ".pi" / "prompts" / "agent-pm.md").is_file()
        assert (adapter_root / ".agents" / "skills" / "demo" / "SKILL.md").is_file()
        assert (adapter_root / "AGENTS.md").is_file()
        assert not (adapter_root / ".codex").exists()
        assert not (adapter_root / ".claude").exists()

        report = json.loads(
            (adapter_root / "adapt-report.json").read_text(encoding="utf-8")
        )
        assert report["adapters"] == ["pi"]
        assert any(
            entry["path"] == ".pi/APPEND_SYSTEM.md"
            for entry in report["entries"]
        )
        shared_skill = next(
            entry
            for entry in report["entries"]
            if entry["path"] == ".agents/skills/demo/SKILL.md"
        )
        assert shared_skill["target"] == "pi"
        assert shared_skill["targets"] == ["pi"]

    def test_adapt_only_claude_does_not_emit_agents_md(self, adapter_root):
        from one_context.cli import _cmd_adapt, build_parser

        parser = build_parser()
        args = parser.parse_args([
            "--root",
            str(adapter_root),
            "adapt",
            "dev",
            "--only",
            "claude_code",
        ])
        assert _cmd_adapt(adapter_root, args) == 0
        assert not (adapter_root / "AGENTS.md").exists()

    def test_pi_append_system_preserves_handwritten_file_without_force(
        self, adapter_root
    ):
        from one_context.cli import build_parser, _cmd_adapt

        target = adapter_root / ".pi" / "APPEND_SYSTEM.md"
        target.parent.mkdir(parents=True)
        target.write_text("# user-owned Pi prompt\n", encoding="utf-8")

        parser = build_parser()
        normal = parser.parse_args([
            "--root", str(adapter_root), "adapt", "dev", "--only", "pi",
        ])
        assert _cmd_adapt(adapter_root, normal) == 0
        assert target.read_text(encoding="utf-8") == "# user-owned Pi prompt\n"

        # A skipped report entry must never claim ownership on the next run.
        assert _cmd_adapt(adapter_root, normal) == 0
        assert target.read_text(encoding="utf-8") == "# user-owned Pi prompt\n"
        report = json.loads(
            (adapter_root / "adapt-report.json").read_text(encoding="utf-8")
        )
        append_entry = next(
            entry
            for entry in report["entries"]
            if entry["path"] == ".pi/APPEND_SYSTEM.md"
        )
        assert append_entry["file_status"] == "skipped_user_edited"

        forced = parser.parse_args([
            "--root", str(adapter_root), "adapt", "dev", "--only", "pi", "--force",
        ])
        assert _cmd_adapt(adapter_root, forced) == 0
        assert "GENERATED by onecxt adapt" in target.read_text(encoding="utf-8")

    def test_workspace_cli_lookup_uses_canonical_id_in_project_manifest(
        self, adapter_root
    ):
        from one_context.cli import _cmd_adapt, build_parser

        parser = build_parser()
        args = parser.parse_args([
            "--root",
            str(adapter_root),
            "adapt",
            "DEV",
            "--only",
            "opencode",
        ])
        assert _cmd_adapt(adapter_root, args) == 0

        config = json.loads(
            (adapter_root / ".opencode/opencode.json").read_text(encoding="utf-8")
        )
        assert config["instructions"] == [
            ".opencode/instructions/onecxt-dev.md"
        ]
        assert (adapter_root / config["instructions"][0]).is_file()

    def test_pi_check_blocks_unsafe_orphan_prompt_symlink(
        self, adapter_root, tmp_path, capsys
    ):
        from one_context.adapters._shared_rules import GENERATED_HEADER_MD
        from one_context.cli import _cmd_adapt, build_parser

        parser = build_parser()
        normal = parser.parse_args([
            "--root",
            str(adapter_root),
            "adapt",
            "dev",
            "--only",
            "pi",
        ])
        assert _cmd_adapt(adapter_root, normal) == 0

        outside = tmp_path / "outside-agent-ghost.md"
        outside.write_text(GENERATED_HEADER_MD + "\n", encoding="utf-8")
        linked = adapter_root / ".pi/prompts/agent-ghost.md"
        try:
            linked.symlink_to(outside)
        except OSError as exc:  # pragma: no cover - Windows without symlink rights
            pytest.skip(f"symlinks unavailable: {exc}")

        check = parser.parse_args([
            "--root",
            str(adapter_root),
            "adapt",
            "dev",
            "--only",
            "pi",
            "--check",
        ])
        assert _cmd_adapt(adapter_root, check) == 1
        assert ".pi/prompts/agent-ghost.md" in capsys.readouterr().err
        assert outside.is_file()

    def test_adapt_unknown_workspace(self, adapter_root, capsys):
        from one_context.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "nonexistent"])

        from one_context.cli import _cmd_adapt
        rc = _cmd_adapt(adapter_root, args)
        assert rc == 2

    def test_adapt_no_args(self, adapter_root, capsys):
        from one_context.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root), "adapt"])

        from one_context.cli import _cmd_adapt
        rc = _cmd_adapt(adapter_root, args)
        assert rc == 2


# ---------------------------------------------------------------------------
# generate_agents unit tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def agents_and_profiles(adapter_root: Path) -> tuple:
    """Load agents and resolved profiles from adapter_root."""
    from one_context.agents import load_agents
    from one_context.profiles import load_mixins, load_profiles, resolve_profile

    agents, _ = load_agents(adapter_root)
    _, profiles_by_id = load_profiles(adapter_root)
    _, mixins_by_id = load_mixins(adapter_root)

    resolved: dict = {}
    for pid in profiles_by_id:
        try:
            resolved[pid] = resolve_profile(pid, profiles_by_id, mixins_by_id)
        except Exception:
            resolved[pid] = profiles_by_id[pid]

    return adapter_root, agents, resolved


class TestClaudeCodeGenerateAgents:
    def test_generates_agent_file(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("claude_code")
        files = adapter.generate_agents(root, agents, profiles)
        assert len(files) >= 1
        assert any("pm.md" in gf.rel_path for gf in files)

    def test_content_has_instructions(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("claude_code")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "PM agent" in content or "PM Agent" in content

    def test_content_has_artifact_ownership(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("claude_code")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "Artifact Ownership" in content or "spec.md" in content

    def test_content_has_profile_section(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("claude_code")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "Profile" in content

    def test_content_has_knowledge_refs(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("claude_code")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "@knowledge/" in content


class TestCursorGenerateAgents:
    def test_generates_agent_mdc(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("cursor")
        files = adapter.generate_agents(root, agents, profiles)
        assert len(files) >= 1
        assert any("agent-pm.mdc" in gf.rel_path for gf in files)

    def test_has_mdc_frontmatter(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("cursor")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert content.startswith("---\n")
        assert "alwaysApply:" in content

    def test_content_has_instructions(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("cursor")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "PM agent" in content or "PM Agent" in content

    def test_inlines_knowledge(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("cursor")
        files = adapter.generate_agents(root, agents, profiles)
        content = files[0].content
        assert "Conventions" in content


class TestOpenClawGenerateAgents:
    def test_project_agent_json_is_not_generated(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("openclaw")
        files = adapter.generate_agents(root, agents, profiles)
        assert files == []


# ---------------------------------------------------------------------------
# Shared rules identity test
# ---------------------------------------------------------------------------

class TestSharedRulesIdentity:
    """Verify all adapters reference the same PROFILE_RULES object."""

    def test_all_adapters_share_same_rules(self):
        from one_context.adapters._shared_rules import PROFILE_RULES as shared
        from one_context.adapters.claude_code import PROFILE_RULES as cc
        from one_context.adapters.codex import PROFILE_RULES as cx
        from one_context.adapters.cursor import PROFILE_RULES as cu
        from one_context.adapters.hermes import PROFILE_RULES as hm
        from one_context.adapters.openclaw import PROFILE_RULES as oc
        from one_context.adapters.opencode import PROFILE_RULES as op
        from one_context.adapters.pi import PROFILE_RULES as pi
        assert cc is shared
        assert cx is shared
        assert cu is shared
        assert hm is shared
        assert oc is shared
        assert op is shared
        assert pi is shared

    def test_shared_rules_count(self):
        from one_context.adapters._shared_rules import PROFILE_RULES
        assert len(PROFILE_RULES) == 15

    def test_shared_rules_cover_efficient_plain_visual_output(self):
        from one_context.adapters._rules import match_rules
        from one_context.adapters._shared_rules import PROFILE_RULES

        profile = {
            "output_style": {
                "tone": "minimal",
                "plain_language": True,
                "visuals": "when_helpful",
            },
        }
        outputs = "\n".join(rule.output for rule in match_rules(profile, PROFILE_RULES))

        assert "include only the information needed" in outputs
        assert "Use plain, everyday language" in outputs
        assert "visualizations only when they materially improve" in outputs


# ---------------------------------------------------------------------------
# Golden tests — output stability after shared-rules migration
# ---------------------------------------------------------------------------

class TestGoldenOutput:
    """Verify generated output contains unified wording and GENERATED marker."""

    def test_claude_code_workspace_has_marker(self, adapter_root, dev_context):
        adapter = get_adapter("claude_code")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content
        assert "GENERATED by onecxt adapt" in content

    def test_claude_code_workspace_has_unified_rules(self, adapter_root, dev_context):
        adapter = get_adapter("claude_code")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content
        assert "You may edit code directly without creating a formal plan first." in content
        assert "Write targeted tests for the specific changes you make." in content

    def test_cursor_workspace_has_marker(self, adapter_root, dev_context):
        adapter = get_adapter("cursor")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content
        assert "GENERATED by onecxt adapt" in content

    def test_cursor_workspace_has_unified_rules(self, adapter_root, dev_context):
        adapter = get_adapter("cursor")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content
        assert "You may edit code directly without creating a formal plan first." in content

    def test_openclaw_workspace_has_marker(self, adapter_root, dev_context):
        adapter = get_adapter("openclaw")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        data = json.loads(files[0].content)
        assert "_generated" in data
        assert "GENERATED by onecxt adapt" in data["_generated"]

    def test_openclaw_workspace_has_unified_rules(self, adapter_root, dev_context):
        adapter = get_adapter("openclaw")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        data = json.loads(files[0].content)
        assert any("without creating a formal plan" in i for i in data["instructions"])
        assert any("targeted tests for the specific changes" in i for i in data["instructions"])

    def test_openclaw_manifest_documents_agent_limitation(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("openclaw")
        files = adapter.generate_project_artifacts(root, ["dev"], agents)
        assert any(
            "per-skill or per-agent JSON" in limitation
            for limitation in files[0].limitations
        )

    def test_openclaw_project_manifest_has_marker(self, agents_and_profiles):
        root, agents, profiles = agents_and_profiles
        adapter = get_adapter("openclaw")
        files = adapter.generate_project_artifacts(root, ["dev"], agents)
        data = json.loads(files[0].content)
        assert "_generated" in data

    def test_hermes_workspace_has_marker(self, adapter_root, dev_context):
        adapter = get_adapter("hermes")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content
        assert "GENERATED by onecxt adapt" in content

    def test_opencode_workspace_has_marker(self, adapter_root, dev_context):
        adapter = get_adapter("opencode")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        assert "GENERATED by onecxt adapt" in files[0].content

    def test_opencode_workspace_has_unified_rules(self, adapter_root, dev_context):
        adapter = get_adapter("opencode")
        ws = dev_context["workspace"]
        files = adapter.generate(adapter_root, ws, dev_context)
        content = files[0].content
        assert "You may edit code directly without creating a formal plan first." in content
        assert "Write targeted tests for the specific changes you make." in content

    def test_opencode_minimal_tone_override(self, minimal_root, minimal_context):
        """opencode adapter override for output_style.tone: minimal renders inline."""
        adapter = get_adapter("opencode")
        ws = minimal_context["workspace"]
        content = adapter.generate(minimal_root, ws, minimal_context)[0].content
        assert "include only the information needed" in content
        assert "Use plain, everyday language" in content
        assert "visualizations only when they materially improve" in content

    def test_pi_workspace_has_marker_and_unified_rules(
        self, adapter_root, dev_context
    ):
        adapter = get_adapter("pi")
        adapter.generate(adapter_root, dev_context["workspace"], dev_context)
        content = adapter.generate_project_artifacts(
            adapter_root, ["dev"], []
        )[0].content
        assert "GENERATED by onecxt adapt" in content
        assert "You may edit code directly without creating a formal plan first." in content
        assert "Write targeted tests for the specific changes you make." in content


# ---------------------------------------------------------------------------
# --check mode tests
# ---------------------------------------------------------------------------

class TestAdaptCheck:
    def test_check_passes_when_fresh(self, adapter_root):
        from one_context.cli import build_parser, _cmd_adapt

        parser = build_parser()
        # First generate
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev"])
        assert _cmd_adapt(adapter_root, args) == 0
        # Then check
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev", "--check"])
        assert _cmd_adapt(adapter_root, args) == 0

    def test_check_fails_when_stale(self, adapter_root):
        from one_context.cli import build_parser, _cmd_adapt

        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev"])
        assert _cmd_adapt(adapter_root, args) == 0
        # Tamper with a generated file
        (adapter_root / ".claude" / "adapters" / "onecxt-dev.md").write_text(
            "tampered", encoding="utf-8",
        )
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev", "--check"])
        assert _cmd_adapt(adapter_root, args) == 1

    def test_check_fails_when_missing(self, adapter_root):
        from one_context.cli import build_parser, _cmd_adapt

        parser = build_parser()
        # Don't generate first — files are missing
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev", "--check"])
        assert _cmd_adapt(adapter_root, args) == 1

    def test_check_and_dry_run_exclusive(self, adapter_root):
        from one_context.cli import build_parser, _cmd_adapt

        parser = build_parser()
        args = parser.parse_args([
            "--root", str(adapter_root), "adapt", "dev", "--check", "--dry-run",
        ])
        assert _cmd_adapt(adapter_root, args) == 2


# ---------------------------------------------------------------------------
# Dirty detection tests
# ---------------------------------------------------------------------------

class TestDirtyDetection:
    def test_markerless_tamper_is_preserved_as_user_edited(self, adapter_root, capsys):
        from one_context.cli import build_parser, _cmd_adapt

        parser = build_parser()
        args = parser.parse_args(["--root", str(adapter_root), "adapt", "dev"])
        _cmd_adapt(adapter_root, args)
        # Tamper
        (adapter_root / ".claude" / "adapters" / "onecxt-dev.md").write_text(
            "tampered", encoding="utf-8",
        )
        # Re-run adapt (normal mode)
        _cmd_adapt(adapter_root, args)
        captured = capsys.readouterr()
        assert "user_edited" in captured.out.lower()
        assert (
            adapter_root / ".claude" / "adapters" / "onecxt-dev.md"
        ).read_text(encoding="utf-8") == "tampered"
