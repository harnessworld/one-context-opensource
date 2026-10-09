"""Claude Code adapter — generates ``.claude/adapters/`` and root ``CLAUDE.md``.

Consumer frequency (highest): the primary surface for this repo. Idle-context
cost matters here — ``on_demand`` agents are excluded from the root ``@`` list
(see ``generate_project_artifacts``) so they don't load into every session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from one_context.adapters import AdapterBase, GeneratedFile, register
from one_context.adapters._report import source_digest
from one_context.adapters._rules import (
    FieldRule,
    collect_top_rules,
    match_rules,
    render_rules_by_section,
    resolve_rule_placement,
    resolve_rule_output,
)
from one_context.adapters._rules_ref import rules_source_path
from one_context.adapters._shared_rules import GENERATED_HEADER_MD, PROFILE_RULES
from one_context.agents import resolve_agent_knowledge
from one_context.skills import SkillMeta

_ADAPTER_NAME = "claude_code"


def _inline_rules(
    matched: list[FieldRule],
    adapter_name: str,
    heading_level: int = 2,
) -> str:
    """Render rules that are NOT top-placement (i.e. inline only)."""
    inline = [r for r in matched if resolve_rule_placement(r, adapter_name) != "top"]
    return render_rules_by_section(inline, heading_level=heading_level, adapter_name=adapter_name)


@register("claude_code")
class ClaudeCodeAdapter(AdapterBase):
    """Generate per-workspace adapter files and project-root ``CLAUDE.md``."""

    supports_file_ref = True

    def __init__(self) -> None:
        self._top_rules: list[str] = []
        self._skills: list[SkillMeta] = []

    def generate(
        self,
        root: Path,
        workspace: dict[str, Any],
        context: dict[str, Any],
    ) -> list[GeneratedFile]:
        ws_id = workspace.get("id", "unknown")
        parts: list[str] = [
            GENERATED_HEADER_MD,
            "",
            f"# one-context — workspace `{ws_id}`",
            "",
        ]

        # Workspace summary
        ws_ctx = workspace.get("context") or {}
        summary = ws_ctx.get("summary", "")
        if summary:
            parts.append(summary)
            parts.append("")

        focus = ws_ctx.get("focus") or []
        if focus:
            parts.append("## Focus Areas")
            parts.append("")
            for item in focus:
                parts.append(f"- {item}")
            parts.append("")

        # Profile rules — inline only; top-placement rules are collected separately
        for profile in context.get("profiles") or []:
            matched = match_rules(profile, PROFILE_RULES)
            # Collect top-placement rules across all profiles
            self._top_rules.extend(collect_top_rules(matched, _ADAPTER_NAME))
            # Render only inline rules
            inline_text = _inline_rules(matched, _ADAPTER_NAME, heading_level=3)
            if inline_text.strip():
                pname = profile.get("name", profile.get("id", ""))
                parts.append(f"## Profile: {pname}")
                parts.append("")
                desc = profile.get("description", "")
                if desc:
                    parts.append(f"_{desc}_")
                    parts.append("")
                parts.append(inline_text)

        # Knowledge — use @file references (Claude Code supports this)
        knowledge = context.get("knowledge") or []
        existing = [k for k in knowledge if k.get("exists")]
        if existing:
            parts.append("## Knowledge")
            parts.append("")
            parts.append("Read these files for project context:")
            parts.append("")
            for entry in existing:
                parts.append(f"@{entry['path']}")
            parts.append("")

        content = "\n".join(parts)
        return [
            GeneratedFile(
                rel_path=f".claude/adapters/onecxt-{ws_id}.md",
                content=content,
                description=f"Claude Code instructions for workspace {ws_id}",
                source_id=ws_id,
                source_kind="profile",
                target_kind="instructions",
                status="native",
                trigger="@-referenced from CLAUDE.md → loaded by Claude Code at session start",
                source_path=f"meta/workspaces.yaml#{ws_id}",
            ),
        ]

    def generate_agents(
        self,
        root: Path,
        agents: list,
        profiles_by_id: dict,
    ) -> list[GeneratedFile]:
        """Generate ``.claude/agents/{id}.md`` for each agent.

        Claude Code follows ``@file`` references at runtime.  Root
        ``CLAUDE.md`` (from ``generate_project_artifacts``) pulls in these
        files so users need not maintain ``@`` lines by hand.
        """
        files: list[GeneratedFile] = []

        for agent in agents:
            agent_id = agent.get("id", "unknown")
            name = agent.get("name", agent_id)
            description = agent.get("description", "")
            instructions = (agent.get("instructions") or "").strip()
            owns: list[str] = agent.get("owns") or []
            profile_id: str | None = agent.get("profile")

            parts: list[str] = [GENERATED_HEADER_MD, "", f"# {name}", ""]
            if description:
                parts.append(f"_{description}_")
                parts.append("")

            if instructions:
                parts.append(instructions)
                parts.append("")

            if owns:
                parts.append("## Artifact Ownership")
                parts.append("")
                parts.append("This agent creates and maintains:")
                for p in owns:
                    parts.append(f"- `{p}`")
                parts.append("")

            worktree = agent.get("worktree")
            if worktree:
                parts.append("## Worktree Convention")
                parts.append("")
                parts.append(f"- Branch: `{worktree.get('branch_pattern', '')}`")
                parts.append(f"- Path: `{worktree.get('path_pattern', '')}`")
                parts.append(f"- Base branch: `{worktree.get('base_branch', 'main')}`")
                parts.append("")

            deploy_manifest = agent.get("deploy_manifest")
            if deploy_manifest:
                parts.append("## Deploy Manifest")
                parts.append("")
                parts.append(f"Look for `{deploy_manifest}` in each repo root before deploying.")
                parts.append("")

            if profile_id:
                profile = profiles_by_id.get(profile_id.casefold())
                if profile:
                    matched = match_rules(profile, PROFILE_RULES)
                    # Collect top-placement rules from agent profiles too
                    self._top_rules.extend(collect_top_rules(matched, _ADAPTER_NAME))
                    inline_text = _inline_rules(matched, _ADAPTER_NAME, heading_level=3)
                    if inline_text.strip():
                        pname = profile.get("name", profile.get("id", ""))
                        parts.append(f"## Profile: {pname}")
                        parts.append("")
                        parts.append(inline_text)

            knowledge = resolve_agent_knowledge(root, agent)
            existing = [k for k in knowledge if k.get("exists")]
            if existing:
                parts.append("## Knowledge")
                parts.append("")
                parts.append("Read these files for context:")
                parts.append("")
                for entry in existing:
                    if entry.get("type") == "directory":
                        dir_path = Path(entry["absolute_path"])
                        for child in sorted(dir_path.rglob("*.md")):
                            rel = child.relative_to(root).as_posix()
                            parts.append(f"@{rel}")
                    else:
                        parts.append(f"@{entry['path']}")
                parts.append("")

            files.append(
                GeneratedFile(
                    rel_path=f".claude/agents/{agent_id}.md",
                    content="\n".join(parts),
                    description=f"Claude Code agent config for {agent_id}",
                    source_id=agent_id,
                    source_kind="agent",
                    target_kind="agent",
                    status="native",
                    trigger="@-included in CLAUDE.md (default agents); on_demand loaded via /agents",
                    source_path="meta/agents.yaml#" + agent_id,
                )
            )

        return files

    def generate_skills(
        self,
        root: Path,
        skills: list[SkillMeta],
    ) -> list[GeneratedFile]:
        """Generate ``.claude/skills/<name>/SKILL.md`` (directory layout).

        Claude Code indexes the ``/`` autocomplete menu from
        ``.claude/skills/<name>/SKILL.md`` files carrying ``name`` /
        ``description`` frontmatter.  Flat ``<name>.md`` stubs are *not*
        discovered.  We emit a directory-style SKILL.md: frontmatter
        first (so the skill shows up and is invocable), then a single
        ``@`` ref to the canonical source.  The ``@`` ref keeps the body
        DRY *and* anchored at ``skills/<name>/``, so relative asset paths
        in the source body (e.g. ``templates/foo.md``) still resolve.
        """
        import yaml

        self._skills = list(skills)
        files: list[GeneratedFile] = []

        for skill in skills:
            fm = dict(skill.frontmatter)
            # Keep this registration out of Cursor's compatibility imports.
            fm["metadata"] = {**(fm.get("metadata") or {}), "surfaces": "claude"}
            fm.setdefault("name", skill.name)
            if not fm.get("description"):
                first_line = next(
                    (ln.strip() for ln in skill.body.splitlines() if ln.strip()),
                    "",
                )
                fm["description"] = first_line.lstrip("# ").strip() or skill.name

            fm_text = yaml.safe_dump(
                fm,
                allow_unicode=True,
                sort_keys=False,
            ).strip()

            content = "\n".join([
                "---",
                fm_text,
                "---",
                "",
                GENERATED_HEADER_MD,
                "",
                f"# Skill: {skill.name}",
                "",
                f"@{skill.source_path}",
                "",
            ])
            files.append(GeneratedFile(
                rel_path=f".claude/skills/{skill.dir_name}/SKILL.md",
                content=content,
                description=f"Claude Code skill registration for {skill.name}",
                source_id=skill.dir_name,
                source_kind="skill",
                target_kind="skill",
                status="native",
                trigger="@-referenced from CLAUDE.md; /skill-name command; / autocomplete",
                source_digest=source_digest((root / skill.source_path).read_text(encoding="utf-8")),
                source_path=skill.source_path,
            ))

        return files

    def generate_project_artifacts(
        self,
        root: Path,
        workspace_ids: list[str],
        agents: list[dict[str, Any]],
    ) -> list[GeneratedFile]:
        """Generate root ``CLAUDE.md`` as a verbatim copy of ``.onecxt/RULES.md``.

        Project rules are the only thing every session must eagerly load.
        Everything else (workspace adapters, agents, skills) is
        auto-discovered by Claude Code from the ``.claude/`` stub
        directories, so no ``@`` refs are emitted here — keeping the root
        file a direct, drift-free copy of the rules SoT.
        """
        rules_path = root / rules_source_path()
        rules_body = (
            rules_path.read_text(encoding="utf-8").strip() if rules_path.is_file() else ""
        )

        lines = [GENERATED_HEADER_MD, ""]
        if rules_body:
            lines.append(rules_body)

        content = "\n".join(lines).rstrip() + "\n"

        files: list[GeneratedFile] = [
            GeneratedFile(
                rel_path="CLAUDE.md",
                content=content,
                description="Claude Code project root CLAUDE.md (generated; verbatim copy of .onecxt/RULES.md)",
                source_id="claude-md",
                source_kind="rule",
                target_kind="rule",
                status="aggregate",
                trigger="auto-loaded by Claude Code at session start (root CLAUDE.md)",
                limitations=[
                    "verbatim copy of .onecxt/RULES.md; workspace/agent/skill context is auto-discovered, not force-loaded",
                ],
                source_path=".onecxt/RULES.md",
            ),
        ]

        # Reset cached top rules after generating artifacts
        self._top_rules = []
        self._skills = []

        return files
