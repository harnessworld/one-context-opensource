"""Hermes adapter — generates ``.hermes.md`` for Hermes Agent CLI.

Hermes discovers project context by priority (first found wins):
  1. .hermes.md / HERMES.md  (walk to git root)  ← we use this
  2. AGENTS.md / agents.md   (cwd only)
  3. CLAUDE.md / claude.md   (cwd only)
  4. .cursorrules

Hermes only auto-loads ONE project context type, so ``.hermes.md`` is a small
routing index. Detailed workspace, agent, and skill content remains in
``.hermes/`` and is read on demand. This keeps private or irrelevant context
out of every session and enforces Hermes' practical root-context budget.
"""

from __future__ import annotations

import posixpath
import re
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
from one_context.adapters._rules_ref import render_rules_block
from one_context.adapters._shared_rules import GENERATED_HEADER_MD, PROFILE_RULES
from one_context.agents import resolve_agent_knowledge
from one_context.skills import SkillMeta, resolve_skill_body

_ADAPTER_NAME = "hermes"
HERMES_ROOT_CHAR_BUDGET = 20_000


_SKILL_RESOURCE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./$-])"
    r"(?P<path>"
    r"(?:\./|\.\./)*"
    r"(?:[A-Za-z0-9_.@%+~=-]+/)+"
    r"[A-Za-z0-9_.@%+~=-]*"
    r")"
    r"(?P<suffix>[?#][^\s`<>()\[\]{}\"']*)?"
)


def _rebase_skill_resource_references(
    root: Path,
    skill: SkillMeta,
    body: str,
    output_path: str,
) -> str:
    """Rebase canonical-Skill-relative resources for one Hermes output.

    Hermes writes a Skill body to ``.hermes/skills/<name>.md``. Rewrite
    path-like references only when they resolve to an
    existing file or directory inside the canonical Skill bundle.  This
    covers Markdown links/images, reference definitions, inline-code paths,
    and the equivalent paths used in prose or fenced commands while leaving
    URLs, anchors, absolute paths, repo-root paths, and shell-variable paths
    untouched.
    """
    skill_dir = posixpath.dirname(skill.source_path)
    output_dir = posixpath.dirname(output_path) or "."

    def replace(match: re.Match[str]) -> str:
        resource_path = match.group("path")
        suffix = match.group("suffix") or ""
        has_trailing_slash = resource_path.endswith("/")

        canonical_path = posixpath.normpath(
            posixpath.join(skill_dir, resource_path.rstrip("/"))
        )
        if canonical_path == skill_dir or not canonical_path.startswith(f"{skill_dir}/"):
            return match.group(0)
        if not (root / Path(canonical_path)).exists():
            return match.group(0)

        rebased = posixpath.relpath(canonical_path, start=output_dir)
        if has_trailing_slash:
            rebased += "/"
        return f"{rebased}{suffix}"

    return _SKILL_RESOURCE_PATH_RE.sub(replace, body)


def _inline_knowledge_text(root: Path, knowledge: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Read knowledge files and return (source, content) pairs."""
    entries: list[tuple[str, str]] = []
    for item in knowledge:
        if not item.get("exists"):
            continue
        abs_path = Path(item["absolute_path"])
        if abs_path.is_file():
            content = abs_path.read_text(encoding="utf-8").rstrip()
            entries.append((item["path"], content))
        elif abs_path.is_dir():
            for child in sorted(abs_path.rglob("*.md")):
                child_rel = child.relative_to(root).as_posix()
                content = child.read_text(encoding="utf-8").rstrip()
                entries.append((child_rel, content))
    return entries


def _inline_rules(
    matched: list[FieldRule],
    adapter_name: str,
    heading_level: int = 2,
) -> str:
    """Render rules that are NOT top-placement (i.e. inline only)."""
    inline = [r for r in matched if resolve_rule_placement(r, adapter_name) != "top"]
    return render_rules_by_section(inline, heading_level=heading_level, adapter_name=adapter_name)


@register("hermes")
class HermesAdapter(AdapterBase):
    """Generate ``.hermes.md`` for Hermes Agent CLI.

    The auto-loaded root file routes to detailed files under ``.hermes/``.
    """

    supports_file_ref = False

    def __init__(self) -> None:
        self._top_rules: list[str] = []
        self._skill_routes: list[tuple[str, str]] = []

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

        # Profile rules — inline only; top-placement rules collected separately
        for profile in context.get("profiles") or []:
            matched = match_rules(profile, PROFILE_RULES)
            self._top_rules.extend(collect_top_rules(matched, _ADAPTER_NAME))
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

        # Knowledge — inline (Hermes does not support @file references)
        knowledge = context.get("knowledge") or []
        knowledge_entries = _inline_knowledge_text(root, knowledge)
        if knowledge_entries:
            parts.append("## Knowledge")
            parts.append("")
            for source, content in knowledge_entries:
                parts.append(f"<!-- source: {source} -->")
                parts.append(content)
                parts.append("")

        # Project Rules — path-only references to rules/*.md (no inline).
        # The LLM uses its Read tool on demand; rules/ stays the single SoT.
        rules_block = render_rules_block(root)
        if rules_block:
            parts.append(rules_block)

        content = "\n".join(parts)
        return [
            GeneratedFile(
                rel_path=f".hermes/onecxt-{ws_id}.md",
                content=content,
                description=f"Hermes context for workspace {ws_id}",
                source_id=ws_id,
                source_kind="profile",
                target_kind="instructions",
                status="native",
                trigger="per-workspace file (also inlined into .hermes.md aggregated root)",
                source_path=f"meta/workspaces.yaml#{ws_id}",
            ),
        ]

    def generate_agents(
        self,
        root: Path,
        agents: list,
        profiles_by_id: dict,
    ) -> list[GeneratedFile]:
        """Generate ``.hermes/agents/{id}.md`` for each agent."""
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
                    self._top_rules.extend(collect_top_rules(matched, _ADAPTER_NAME))
                    inline_text = _inline_rules(matched, _ADAPTER_NAME, heading_level=3)
                    if inline_text.strip():
                        pname = profile.get("name", profile.get("id", ""))
                        parts.append(f"## Profile: {pname}")
                        parts.append("")
                        parts.append(inline_text)

            knowledge = resolve_agent_knowledge(root, agent)
            knowledge_entries = _inline_knowledge_text(root, knowledge)
            if knowledge_entries:
                parts.append("## Knowledge")
                parts.append("")
                for source, content in knowledge_entries:
                    parts.append(f"<!-- source: {source} -->")
                    parts.append(content)
                    parts.append("")

            agent_content = "\n".join(parts)
            files.append(
                GeneratedFile(
                    rel_path=f".hermes/agents/{agent_id}.md",
                    content=agent_content,
                    description=f"Hermes agent config for {agent_id}",
                    source_id=agent_id,
                    source_kind="agent",
                    target_kind="agent",
                    status="native",
                    trigger="per-agent file (also inlined into .hermes.md aggregated root)",
                    source_path="meta/agents.yaml#" + agent_id,
                )
            )

        return files

    def generate_skills(
        self,
        root: Path,
        skills: list[SkillMeta],
    ) -> list[GeneratedFile]:
        """Generate ``.hermes/skills/<name>.md`` for each skill.

        Hermes does not support ``@file`` references, so the full
        SKILL.md body is written to its native per-skill file.
        """
        files: list[GeneratedFile] = []

        for skill in skills:
            body = resolve_skill_body(root, skill)
            skill_output_path = f".hermes/skills/{skill.dir_name}.md"
            per_skill_body = _rebase_skill_resource_references(
                root,
                skill,
                body,
                skill_output_path,
            )
            per_skill_parts = [
                GENERATED_HEADER_MD,
                "",
                f"# Skill: {skill.name}",
                "",
                per_skill_body,
            ]
            skill_content = "\n".join(per_skill_parts)
            self._skill_routes.append((skill.name, skill_output_path))

            files.append(GeneratedFile(
                rel_path=skill_output_path,
                content=skill_content,
                description=f"Hermes skill config for {skill.name}",
                source_id=skill.dir_name,
                source_kind="skill",
                target_kind="skill",
                status="native",
                trigger="per-skill file (also inlined into .hermes.md aggregated root)",
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
        """Generate the budgeted project-root routing index ``.hermes.md``."""
        del root  # paths are repo-relative

        # Deduplicate top rules while preserving order
        seen: set[str] = set()
        unique_top: list[str] = []
        for rule_text in self._top_rules:
            if rule_text not in seen:
                seen.add(rule_text)
                unique_top.append(rule_text)

        # Top-placement hard rules at the very top
        top_block = ""
        if unique_top:
            top_block = "\n".join(unique_top) + "\n\n"

        # Keep the auto-loaded root small; detailed context is available in
        # the native files below and should be read only when relevant.
        body_parts: list[str] = [
            GENERATED_HEADER_MD,
            "",
            "# one-context — Hermes",
            "",
            "This file is **generated by** `onecxt adapt`. Do not edit by hand.",
            "",
            "Load only the detailed context needed for the current task.",
            "",
            "## Workspaces",
            "",
        ]

        if workspace_ids:
            for ws_id in workspace_ids:
                body_parts.append(
                    f"- Workspace `{ws_id}`: read `.hermes/onecxt-{ws_id}.md`."
                )
        else:
            body_parts.append("- No workspace selected.")
        body_parts.extend(["", "## Agents", ""])

        default_agents = [agent for agent in agents if not agent.get("on_demand")]
        on_demand_agents = [agent for agent in agents if agent.get("on_demand")]
        for agent in default_agents:
            agent_id = agent.get("id", "unknown")
            name = agent.get("name", agent_id)
            body_parts.append(
                f"- `{agent_id}` ({name}): read `.hermes/agents/{agent_id}.md`."
            )
        if not default_agents:
            body_parts.append("- No default agents registered.")

        if on_demand_agents:
            body_parts.extend(["", "### On-demand agents", ""])
            body_parts.append("Read these only when the role is explicitly needed:")
            for agent in on_demand_agents:
                agent_id = agent.get("id", "unknown")
                name = agent.get("name", agent_id)
                body_parts.append(
                    f"- `{agent_id}` ({name}): `.hermes/agents/{agent_id}.md`."
                )

        body_parts.extend(["", "## Skills", ""])
        body_parts.append("Before using a skill, read its complete file:")
        for skill_name, skill_path in self._skill_routes:
            body_parts.append(f"- `{skill_name}`: `{skill_path}`")
        if not self._skill_routes:
            body_parts.append("- No project skills registered.")

        content = top_block + "\n".join(body_parts).rstrip() + "\n"
        if len(content) > HERMES_ROOT_CHAR_BUDGET:
            raise ValueError(
                "Hermes root context exceeds the 20,000-character budget: "
                f"{len(content):,} characters"
            )

        files: list[GeneratedFile] = [
            GeneratedFile(
                rel_path=".hermes.md",
                content=content,
                description="Hermes project context (auto-discovered)",
                source_id="hermes-md",
                source_kind="overview",
                target_kind="overview",
                status="aggregate",
                trigger="Hermes walks to git root to find .hermes.md — only ONE context file is loaded",
                limitations=[
                    "root context is a routing index; detailed files are read on demand",
                    "on_demand agent instructions are never inlined into root context",
                    "root context is limited to 20,000 characters",
                ],
                source_path="meta/workspaces.yaml + meta/agents.yaml + skills/",
            ),
        ]

        # Reset cached state after generating artifacts
        self._top_rules = []
        self._skill_routes = []

        return files
