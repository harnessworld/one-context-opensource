"""Pi coding agent adapter — generates the project-local ``.pi/`` tree.

Pi automatically loads root ``AGENTS.md`` plus trusted project resources.
The tool-neutral ``AGENTS.md`` remains owned by the common project-artifact
pipeline; this adapter adds Pi-native workspace context and prompt templates:

* ``.pi/APPEND_SYSTEM.md`` — all selected workspace/profile context, appended
  to Pi's default system prompt after project trust.
* ``.pi/prompts/agent-<id>.md`` — slash prompt that activates a one-context
  role in the current Pi session.
* ``.agents/skills/<name>/SKILL.md`` — shared native Agent Skills projection,
  emitted once by the common orchestrator for both Pi and Codex.

Pi prompt templates are not isolated custom agents or subagents, so agent
projection is deliberately reported as ``translate`` rather than ``native``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from one_context.adapters import AdapterBase, GeneratedFile, register
from one_context.adapters._report import STATUS_AGGREGATE, STATUS_TRANSLATE, source_digest
from one_context.adapters._rules import match_rules, render_rules_by_section
from one_context.adapters._shared_rules import GENERATED_HEADER_MD, PROFILE_RULES
from one_context.agents import resolve_agent_knowledge

_ADAPTER_NAME = "pi"


def _escape_pi_template_literals(text: str) -> str:
    """Preserve literal ``$`` in Pi prompt bodies during argument expansion.

    Pi substitutes positional placeholders across the entire prompt template.
    ``${0:-$}`` evaluates to a literal dollar sign and replacements are not
    recursively expanded, so canonical instructions such as ``tool $1`` or
    ``echo $ARGUMENTS`` remain byte-for-byte meaningful after invocation.
    """
    return text.replace("$", "${0:-$}")


def _short_alias_description(skill: Any) -> str:
    """Build a concise autocomplete description for a ``/<alias>`` prompt.

    Uses the skill's frontmatter ``description`` (first sentence, clipped) and
    always names the canonical ``/skill:<name>`` invocation so users see the
    relationship in Pi's ``/`` autocomplete dropdown.
    """
    raw = ""
    fm_desc = skill.frontmatter.get("description")
    if isinstance(fm_desc, str):
        raw = fm_desc
    if not raw.strip():
        raw = skill.name
    first = next(
        (ln.strip() for ln in raw.splitlines() if ln.strip()),
        raw,
    )
    if "." in first:
        first = first.split(".", 1)[0]
    first = " ".join(first.split())
    if len(first) > 80:
        first = first[:77].rstrip() + "..."
    return f"/{skill.name} alias  → /skill:{skill.name}. {first}".strip()


def _knowledge_paths(knowledge: list[dict[str, Any]]) -> str:
    """Render traceable, on-demand knowledge paths without eager duplication."""
    paths = [item.get("path", "") for item in knowledge if item.get("exists")]
    if not paths:
        return ""
    lines = [
        "Read the following canonical project knowledge when it is relevant:",
        "",
    ]
    lines.extend(f"- `{path}`" for path in paths if path)
    return "\n".join(lines).rstrip()


def _profile_block(profile: dict[str, Any], *, heading_level: int = 3) -> str:
    matched = match_rules(profile, PROFILE_RULES)
    rendered = render_rules_by_section(
        matched,
        heading_level=heading_level,
        adapter_name=_ADAPTER_NAME,
    )
    if not rendered.strip():
        return ""
    name = profile.get("name", profile.get("id", ""))
    parts = [f"## Profile: {name}", ""]
    description = profile.get("description", "")
    if description:
        parts.extend([f"_{description}_", ""])
    parts.append(rendered.rstrip())
    return "\n".join(parts)


@register(_ADAPTER_NAME)
class PiAdapter(AdapterBase):
    """Generate Pi-native project resources in an independent adapter module."""

    supports_file_ref = False
    supports_agent_skills = True
    supports_agents_md = True

    def __init__(self) -> None:
        self._workspace_sections: list[str] = []

    def generate_skills(
        self,
        root: Path,
        skills: list[Any],
    ) -> list[GeneratedFile]:
        """Emit thin ``/<alias>`` Pi prompt aliases for opted-in skills.

        Pi exposes Agent Skills only as ``/skill:<name>``.  Skills that declare
        an ``aliases`` frontmatter list (e.g. high-frequency ``sc`` / ``gs``)
        also get a project prompt template so the bare short name resolves as
        ``/<alias>`` instead of colliding with built-in command prefixes.

        The alias body never duplicates skill content: it tells the model to
        ``read`` the canonical ``SKILL.md`` by path, preserving the
        single-source-of-truth contract.  Pruning of these aliases is
        regenerate-overwrite only; they are not enrolled in the agent/skill
        orphan reapers because ``.pi/prompts`` non-``agent-*`` files are
        outside both glob tables by design.
        """
        files: list[GeneratedFile] = []
        for skill in skills:
            aliases = skill.frontmatter.get("aliases")
            if not isinstance(aliases, list):
                continue
            for alias_value in aliases:
                alias = str(alias_value).strip()
                if not alias:
                    continue
                description = _short_alias_description(skill)
                body = _escape_pi_template_literals(
                    "\n".join([
                        f"<!-- canonical-source: {skill.source_path}; "
                        f"alias: {alias} -->",
                        f"# Activate skill: {skill.name} (`/skill:{skill.name}`)",
                        "",
                        f"This `/{alias}` prompt is a short-name alias for the "
                        f"**{skill.name}** Agent Skill. Load and follow the "
                        "canonical skill before doing anything else.",
                        "",
                        f"Use the `read` tool on `{skill.source_path}` and "
                        "follow it for the remainder of this task. Do not act "
                        "until the skill is loaded.",
                        "",
                        "Arguments: ${ARGUMENTS:-}",
                        "",
                    ]).rstrip()
                )
                # Keep the live argument token unescaped so Pi expands it.
                body = body.replace(
                    "${0:-$}{ARGUMENTS:-}", "${ARGUMENTS:-}"
                )
                frontmatter = yaml.safe_dump(
                    {
                        "description": description,
                        "argument-hint": "[args]",
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ).strip()
                content = "\n".join([
                    "---",
                    frontmatter,
                    "---",
                    "",
                    GENERATED_HEADER_MD,
                    "",
                    body,
                    "",
                ])
                files.append(
                    GeneratedFile(
                        rel_path=f".pi/prompts/{alias}.md",
                        content=content,
                        description=(
                            f"Pi short-name slash prompt aliasing "
                            f"/skill:{skill.name}"
                        ),
                        source_id=skill.dir_name,
                        source_kind="skill",
                        target_kind="prompt",
                        status=STATUS_TRANSLATE,
                        trigger=f"/{alias} [args] after project trust",
                        limitations=(
                            "alias loads the canonical skill via read; it is "
                            "not an isolated subagent",
                            "project-local .pi resources require project trust",
                        ),
                        source_path=skill.source_path,
                    )
                )
        return files

    def generate(
        self,
        root: Path,
        workspace: dict[str, Any],
        context: dict[str, Any],
    ) -> list[GeneratedFile]:
        """Accumulate one section per selected workspace for APPEND_SYSTEM.md."""
        del root
        workspace_id = workspace.get("id", "unknown")
        workspace_context = workspace.get("context") or {}
        parts = [f"## Workspace: `{workspace_id}`", ""]

        summary = workspace_context.get("summary", "")
        if summary:
            parts.extend([summary, ""])

        focus = workspace_context.get("focus") or []
        if focus:
            parts.extend(["### Focus Areas", ""])
            parts.extend(f"- {item}" for item in focus)
            parts.append("")

        for profile in context.get("profiles") or []:
            block = _profile_block(profile)
            if block:
                parts.extend([block, ""])

        knowledge = _knowledge_paths(context.get("knowledge") or [])
        if knowledge:
            parts.extend(["### Workspace Knowledge", "", knowledge, ""])

        self._workspace_sections.append("\n".join(parts).rstrip())
        # Pi has one aggregate startup surface, emitted after all workspaces.
        return []

    def generate_agents(
        self,
        root: Path,
        agents: list[dict[str, Any]],
        profiles_by_id: dict[str, dict[str, Any]],
    ) -> list[GeneratedFile]:
        """Generate flat Pi prompt templates, invoked as ``/agent-<id>``."""
        files: list[GeneratedFile] = []
        for agent in agents:
            agent_id = agent.get("id", "unknown")
            name = agent.get("name", agent_id)
            description = agent.get("description", "")
            instructions = (agent.get("instructions") or "").strip()
            ownership: list[str] = agent.get("owns") or []

            prompt: list[str] = [
                GENERATED_HEADER_MD,
                "",
                f"# Activate one-context agent: {name}",
                "",
                "Adopt this role for the current task in this Pi session.",
                "This is a prompt-based role switch, not an isolated subagent.",
                "",
            ]
            if description:
                prompt.extend([description, ""])
            if instructions:
                prompt.extend(["## Instructions", "", instructions, ""])
            if ownership:
                prompt.extend([
                    "## Artifact Ownership",
                    "",
                    "Create and maintain:",
                ])
                prompt.extend(f"- `{path}`" for path in ownership)
                prompt.append("")

            worktree = agent.get("worktree")
            if worktree:
                prompt.extend([
                    "## Worktree Convention",
                    "",
                    f"- Branch: `{worktree.get('branch_pattern', '')}`",
                    f"- Path: `{worktree.get('path_pattern', '')}`",
                    f"- Base branch: `{worktree.get('base_branch', 'main')}`",
                    "",
                ])

            deploy_manifest = agent.get("deploy_manifest")
            if deploy_manifest:
                prompt.extend([
                    "## Deploy Manifest",
                    "",
                    f"Read `{deploy_manifest}` in each repo root before deploying.",
                    "",
                ])

            profile_id = agent.get("profile")
            if profile_id:
                profile = profiles_by_id.get(str(profile_id).casefold())
                if profile:
                    block = _profile_block(profile)
                    if block:
                        prompt.extend([block, ""])

            knowledge = _knowledge_paths(resolve_agent_knowledge(root, agent))
            if knowledge:
                prompt.extend(["## Agent Knowledge", "", knowledge, ""])

            literal_body = _escape_pi_template_literals(
                "\n".join(prompt).rstrip()
            )
            prompt_body = "\n".join([
                literal_body,
                "",
                "## Current Task",
                "",
                "${ARGUMENTS:-Continue with the task described in the conversation.}",
            ])
            frontmatter = yaml.safe_dump(
                {
                    "description": f"Activate {name}: {description}".rstrip(": "),
                    "argument-hint": "[task]",
                },
                allow_unicode=True,
                sort_keys=False,
            ).strip()
            content = "\n".join([
                "---",
                frontmatter,
                "---",
                "",
                prompt_body,
                "",
            ])
            files.append(
                GeneratedFile(
                    rel_path=f".pi/prompts/agent-{agent_id}.md",
                    content=content,
                    description=f"Pi prompt template for one-context agent {agent_id}",
                    source_id=agent_id,
                    source_kind="agent",
                    target_kind="prompt",
                    status=STATUS_TRANSLATE,
                    trigger=f"/agent-{agent_id} [task] after project trust",
                    limitations=(
                        "Pi prompt templates switch role in the current session; "
                        "they are not isolated custom agents or subagents",
                        "project-local .pi resources require project trust and "
                        "are discovered from the Pi working directory",
                    ),
                    source_path="meta/agents.yaml#" + agent_id,
                )
            )
        return files

    def generate_project_artifacts(
        self,
        root: Path,
        workspace_ids: list[str],
        agents: list[dict[str, Any]],
    ) -> list[GeneratedFile]:
        """Emit the single Pi append-system prompt after workspace collection."""
        del root, agents
        if not self._workspace_sections:
            return []
        content = "\n".join([
            GENERATED_HEADER_MD,
            "",
            "# one-context — Pi workspace context",
            "",
            "Pi already loads the repository's root `AGENTS.md`. The sections below",
            "add the selected one-context workspace and profile context.",
            "",
            "\n\n".join(self._workspace_sections),
            "",
        ])
        self._workspace_sections = []
        return [
            GeneratedFile(
                rel_path=".pi/APPEND_SYSTEM.md",
                content=content,
                description=(
                    "Pi aggregate workspace context appended to the default "
                    "system prompt"
                ),
                source_id="pi-append-system",
                source_kind="overview",
                target_kind="instructions",
                status=STATUS_AGGREGATE,
                trigger="auto-loaded by Pi at startup after project trust",
                limitations=(
                    "project-local .pi resources require project trust",
                    "Pi discovers .pi resources from its working directory; "
                    "launch Pi at the repository root",
                ),
                source_path=(
                    "meta/workspaces.yaml#" + ",".join(workspace_ids)
                    if workspace_ids
                    else "meta/workspaces.yaml"
                ),
            )
        ]
