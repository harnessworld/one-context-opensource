"""Codex CLI adapter — generates ``.codex/`` project tree.

OpenAI Codex reads project context from ``AGENTS.md``, project Agent
Skills from ``.agents/skills/`` (REPO scope, autoloaded), and project-scoped
custom agents from ``.codex/agents/*.toml``.  This adapter emits all of
them directly so Codex consumes the project tree without any global
install step.

Layout produced by this adapter:

* ``.codex/instructions/onecxt-<ws>.md``  — workspace rules + inlined
  knowledge (Codex AGENTS.md does NOT ``@file``-include, so per-workspace
  state lives here for ``@``-reference from a hand-maintained ``AGENTS.md``,
  or for archival / diff-friendly version control).
* ``.agents/skills/<name>/SKILL.md``    — shared Codex/Pi thin shells emitted
  once by the common project-artifact pipeline (autoloaded by Codex as
  REPO-scope skills with ``name`` / ``description`` / ``allowed-tools``).
* ``.codex/agents/<id>.toml``           — project-scoped custom agents in
  Codex-native TOML (``name`` / ``description`` / ``developer_instructions``),
  loadable via ``/agent`` without any global install step.

Codex-specific frontmatter key ``allowed-tools`` is emitted with a
sensible default (``Read, Write, Edit, Bash, Grep, Glob``) whenever the
source skill frontmatter omits it, matching the convention seen in
``~/.codex/skills/bigfish/SKILL.md`` and other shipping Codex skills.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from one_context.adapters import AdapterBase, GeneratedFile, register
from one_context.adapters._report import ReportEntry, STATUS_UNSUPPORTED
from one_context.adapters._rules import match_rules, render_rules_by_section
from one_context.adapters._rules_ref import render_rules_block
from one_context.adapters._shared_rules import GENERATED_HEADER_MD, PROFILE_RULES
from one_context.agents import resolve_agent_knowledge
from one_context.skills import SkillMeta

_ADAPTER_NAME = "codex"

# Codex-CLI-native asset kinds that onecxt deliberately does NOT distribute
# via this adapter. Surface as explicit `status: unsupported` in the report
# (AC7 of adapter-compat-matrix) rather than silent skips.
_UNSUPPORTED_KINDS_BY_CODEX: tuple[tuple[str, str, str], ...] = (
    # (source_kind, why_skipped, where_user_would_define_it)
    (
        "command",
        "onecxt 不分发 commands — 见 spec 非目标",
        "用户在 ~/.codex/commands/ 自行定义（Codex 无项目级 commands 约定）",
    ),
)
# NOTE: `rule` is deliberately NOT listed — rules are distributed
# uniformly across all adapters: `.onecxt/RULES.md` is inlined into every
# startup surface (shared root `AGENTS.md` + this adapter's instructions
# file via `_rules_ref.render_rules_block`). Codex follows the same
# path as every other platform.

def _inline_knowledge_md(root: Path, knowledge: list[dict[str, Any]]) -> str:
    """Render knowledge files/dirs as inlined fenced markdown blocks.

    Codex does not follow ``@file`` references, so any knowledge we want
    surfaced in this adapter's files must be inlined.  Each entry is
    preceded by a ``### file: <path>`` header so the source stays
    traceable.

    Mirrors the OpenCode adapter's helper; kept local so this adapter
    stays independent of other adapters'private helpers.
    """
    parts: list[str] = []
    for item in knowledge:
        if not item.get("exists"):
            continue
        abs_path = Path(item["absolute_path"])
        if abs_path.is_file():
            parts.append(f"### file: `{item['path']}`")
            parts.append("")
            parts.append(abs_path.read_text(encoding="utf-8").rstrip())
            parts.append("")
        elif abs_path.is_dir():
            for child in sorted(abs_path.rglob("*.md")):
                rel = child.relative_to(root).as_posix()
                parts.append(f"### file: `{rel}`")
                parts.append("")
                parts.append(child.read_text(encoding="utf-8").rstrip())
                parts.append("")
    return "\n".join(parts).rstrip()


@register(_ADAPTER_NAME)
class CodexAdapter(AdapterBase):
    """Generate ``.codex/`` project config tree for OpenAI Codex CLI.

    All assets land in the project tree and are consumed natively by
    Codex CLI — no global ``~/.codex`` install step is required:

    * ``AGENTS.md`` at the repo root (already produced by the tool-neutral
      ``project_artifacts`` pipeline) is Codex's project-context entry.
    * ``.agents/skills/`` thin shells are autoloaded as REPO-scope Agent
      Skills (skills only reference the canonical bodies under ``skills/``).
    * ``.codex/agents/<id>.toml`` files are project-scoped custom agents,
      selectable via ``/agent`` inline at their point of use.

    * ``.codex/instructions/onecxt-<ws>.md`` remains a portable per-workspace
      mirror for ``@``-reference or archival, since Codex AGENTS.md does not
      ``@file``-include.
    """

    supports_file_ref = False
    supports_agent_skills = True
    supports_agents_md = True

    def __init__(self) -> None:
        self._workspace_ids: list[str] = []
        self._skills: list[SkillMeta] = []

    def generate(
        self,
        root: Path,
        workspace: dict[str, Any],
        context: dict[str, Any],
    ) -> list[GeneratedFile]:
        ws_id = workspace.get("id", "unknown")
        self._workspace_ids.append(ws_id)
        ws_ctx = workspace.get("context") or {}

        parts: list[str] = [
            GENERATED_HEADER_MD,
            "",
            f"# one-context — workspace `{ws_id}` (Codex CLI)",
            "",
        ]
        summary = ws_ctx.get("summary", "")
        if summary:
            parts.extend([summary, ""])

        focus = ws_ctx.get("focus") or []
        if focus:
            parts.append("## Focus Areas")
            parts.append("")
            for item in focus:
                parts.append(f"- {item}")
            parts.append("")

        for profile in context.get("profiles") or []:
            matched = match_rules(profile, PROFILE_RULES)
            inline_text = render_rules_by_section(
                matched, heading_level=3, adapter_name=_ADAPTER_NAME
            )
            if inline_text.strip():
                pname = profile.get("name", profile.get("id", ""))
                parts.append(f"## Profile: {pname}")
                parts.append("")
                desc = profile.get("description", "")
                if desc:
                    parts.append(f"_{desc}_")
                    parts.append("")
                parts.append(inline_text)

        knowledge = context.get("knowledge") or []
        knowledge_md = _inline_knowledge_md(root, knowledge)
        if knowledge_md:
            parts.append("## Knowledge")
            parts.append("")
            parts.append(knowledge_md)
            parts.append("")

        # Project Rules — path-only references to rules/*.md (no inline).
        # The LLM uses its Read tool on demand; rules/ stays the single SoT.
        rules_block = render_rules_block(root)
        if rules_block:
            parts.append(rules_block)

        return [
            GeneratedFile(
                rel_path=f".codex/instructions/onecxt-{ws_id}.md",
                content="\n".join(parts).rstrip() + "\n",
                description=f"Codex instructions for workspace {ws_id}",
                source_id=ws_id,
                source_kind="profile",
                target_kind="instructions",
                status="native",
                trigger=(
                    "not autoloaded by Codex CLI — kept for @-reference "
                    "from a hand-maintained AGENTS.md or archival use"
                ),
                source_path=f"meta/workspaces.yaml#{ws_id}",
            ),
        ]

    def generate_agents(
        self,
        root: Path,
        agents: list,
        profiles_by_id: dict,
    ) -> list[GeneratedFile]:
        """Generate ``.codex/agents/<id>.toml`` for each one-context agent.

        Codex loads project-scoped custom agents natively from
        ``.codex/agents/*.toml`` (same shape as ``~/.codex/agents/*.toml``,
        but scoped to the repo) — no global install step is needed.
        Each file sets ``name`` / ``description`` and embeds the full
        markdown prompt body as ``developer_instructions``.
        """
        import json

        def _toml_str(value: str) -> str:
            # JSON string escaping is a subset of valid TOML basic-string
            # escaping, so json.dumps gives us a safe quoted TOML string.
            return json.dumps(value, ensure_ascii=False)

        files: list[GeneratedFile] = []

        for agent in agents:
            agent_id = agent.get("id", "unknown")
            name = agent.get("name", agent_id)
            description = agent.get("description", "")
            instructions = (agent.get("instructions") or "").strip()
            owns: list[str] = agent.get("owns") or []
            profile_id: str | None = agent.get("profile")

            body: list[str] = [GENERATED_HEADER_MD, "", f"# {name}", ""]
            if description:
                body.append(f"_{description}_")
                body.append("")
            if instructions:
                body.append(instructions)
                body.append("")
            if owns:
                body.append("## Artifact Ownership")
                body.append("")
                body.append("This agent creates and maintains:")
                for p in owns:
                    body.append(f"- `{p}`")
                body.append("")

            worktree = agent.get("worktree")
            if worktree:
                body.append("## Worktree Convention")
                body.append("")
                body.append(f"- Branch: `{worktree.get('branch_pattern', '')}`")
                body.append(f"- Path: `{worktree.get('path_pattern', '')}`")
                body.append(f"- Base branch: `{worktree.get('base_branch', 'main')}`")
                body.append("")

            deploy_manifest = agent.get("deploy_manifest")
            if deploy_manifest:
                body.append("## Deploy Manifest")
                body.append("")
                body.append(
                    f"Look for `{deploy_manifest}` in each repo root before deploying."
                )
                body.append("")

            if profile_id:
                profile = profiles_by_id.get(profile_id.casefold())
                if profile:
                    matched = match_rules(profile, PROFILE_RULES)
                    inline_text = render_rules_by_section(
                        matched, heading_level=3, adapter_name=_ADAPTER_NAME
                    )
                    if inline_text.strip():
                        pname = profile.get("name", profile.get("id", ""))
                        body.append(f"## Profile: {pname}")
                        body.append("")
                        body.append(inline_text)

            knowledge = resolve_agent_knowledge(root, agent)
            knowledge_md = _inline_knowledge_md(root, knowledge)
            if knowledge_md:
                body.append("## Knowledge")
                body.append("")
                body.append(knowledge_md)
                body.append("")

            descriptions = description or f"{name} (one-context agent)"
            content = "\n".join([
                "# GENERATED by onecxt adapt — DO NOT EDIT. "
                "Regenerate with `onecxt adapt --all`.",
                f"name = {_toml_str(agent_id)}",
                f"description = {_toml_str(descriptions)}",
                f"developer_instructions = {_toml_str(chr(10).join(body).rstrip() + chr(10))}",
                "",
            ])
            files.append(
                GeneratedFile(
                    rel_path=f".codex/agents/{agent_id}.toml",
                    content=content,
                    description=f"Codex project-scoped agent for {agent_id}",
                    source_id=agent_id,
                    source_kind="agent",
                    target_kind="agent",
                    status="native",
                    trigger=(
                        "autoloaded by Codex CLI as a project-scoped custom "
                        "agent — selectable via /agent"
                    ),
                    source_path="meta/agents.yaml#" + agent_id,
                )
            )

        return files

    def generate_skills(
        self,
        root: Path,
        skills: list[SkillMeta],
    ) -> list[GeneratedFile]:
        """Record skills; the common pipeline emits shared ``.agents/skills``."""
        self._skills = list(skills)
        return []

    def generate_project_artifacts(
        self,
        root: Path,
        workspace_ids: list[str],
        agents: list[dict[str, Any]],
    ) -> list[GeneratedFile]:
        """Generate ``.codex/README.md`` documenting this project's codex tree.

        Everything Codex needs is produced natively in the project tree —
        root ``AGENTS.md`` (tool-neutral project context), REPO-scope
        skills under ``.agents/skills/``, and project-scoped custom agents
        under ``.codex/agents/*.toml``.  No global ``~/.codex`` install
        step is required; the README just records what is generated.
        """
        if not (workspace_ids or self._skills or agents):
            return []

        readme = "\n".join([
            GENERATED_HEADER_MD,
            "",
            "# Codex CLI project tree (one-context)",
            "",
            "Codex CLI consumes this project natively — no global install:",
            "",
            "- Root `AGENTS.md` — tool-neutral project context, generated by `onecxt adapt`",
            "- `.agents/skills/<name>/SKILL.md` — REPO-scope Agent Skills (autoloaded by Codex)",
            "- `.codex/agents/<id>.toml` — project-scoped custom agents, picked via `/agent`",
            "- `.codex/instructions/onecxt-<ws>.md` — portable per-workspace mirror (archival/`@`-ref only)",
            "",
            "Canonical sources stay in `skills/` + `meta/` + `.onecxt/RULES.md`;",
            "regenerate with `onecxt adapt --all` after editing inputs.",
            "",
            f"Generated for workspaces: {', '.join(workspace_ids) if workspace_ids else '(none)'}",
            f"Generated skills count: {len(self._skills)}",
            f"Generated agents count: {len(agents)}",
            "",
        ])

        # Reset accumulated state — this method is called once per adapt run.
        self._workspace_ids = []
        self._skills = []
        return [
            GeneratedFile(
                rel_path=".codex/README.md",
                content=readme,
                description="Codex CLI project tree README",
                source_id="codex-project-readme",
                source_kind="manifest",
                target_kind="manifest",
                status="aggregate",
                trigger="human reader; documents this project's codex tree",
                limitations=(),
                source_path="meta/workspaces.yaml + skills/",
            )
        ]

    def unsupported_assets(self) -> list[ReportEntry]:
        """Return placeholder entries for asset kinds Codex supports but
        onecxt deliberately does not distribute (commands).

        Per spec AC7 of ``features/core/adapter-compat-matrix/``: an
        explicit unsupported entry beats a silent skip — consumers of
        ``adapt-report.json`` get a complete capability matrix per
        target.  Rules are intentionally NOT declared unsupported — they
        are distributed inline like for every other adapter.
        """
        return [
            ReportEntry(
                source_id=f"<{kind}>",
                source_kind=kind,
                target=_ADAPTER_NAME,
                target_kind=kind,
                status=STATUS_UNSUPPORTED,
                path="",
                trigger="(not distributed)",
                limitations=[why, fallback],
                source_path="",
            )
            for kind, why, fallback in _UNSUPPORTED_KINDS_BY_CODEX
        ]
