"""OpenCode adapter — generates ``.opencode/`` config tree.

OpenCode (sst/opencode) reads, per project:

* ``.opencode/opencode.json``      — config (deep-merged with global ``~/.config/opencode/opencode.json``)
* ``.opencode/agent/<id>.md``      — agents (YAML frontmatter + prompt body)
* ``.opencode/skills/<name>/SKILL.md`` — skills (frontmatter + body, same shape as Claude Code)

Unlike Claude Code, OpenCode does **not** follow ``@file`` references, so
knowledge and agent context is **inlined**; skill shells explicitly instruct
the agent to read the canonical project source.  The auto-generated
``AGENTS.md`` (tool-neutral, produced by ``project_artifacts``) is read by
OpenCode automatically and already carries the project overview + skill
routing table; this adapter only adds the workspace-specific profile rules +
knowledge that ``AGENTS.md`` does not duplicate.

Consumer frequency: on par with Cursor.  Per-agent configs are separate
files (``.opencode/agent/<id>.md``), so on-demand agents impose no idle cost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from one_context.adapters import AdapterBase, GeneratedFile, register
from one_context.adapters._report import ReportEntry, STATUS_UNSUPPORTED, source_digest
from one_context.adapters._rules import match_rules, render_rules_by_section
from one_context.adapters._rules_ref import render_rules_block
from one_context.adapters._shared_rules import GENERATED_HEADER_MD, PROFILE_RULES
from one_context.agents import resolve_agent_knowledge
from one_context.skills import SkillMeta, render_skill_reference_body

_ADAPTER_NAME = "opencode"

# Asset kinds that OpenCode natively supports but onecxt deliberately does NOT
# distribute via this adapter. Declared so adapt-report.json surfaces them as
# explicit ``status: unsupported`` rather than silent skips (spec AC7).
_UNSUPPORTED_KINDS_BY_OPENCODE: tuple[tuple[str, str, str], ...] = (
    # (source_kind, why_skipped, where_user_would_define_it)
    ("command", "onecxt does not distribute commands — out of scope by design", "Users define their own in .opencode/commands/"),
)
# NOTE: `rule` is deliberately NOT listed — rules are distributed uniformly
# across all adapters: `.onecxt/RULES.md` is inlined into every startup
# surface (shared root `AGENTS.md` + this adapter's instructions file via
# `_rules_ref.render_rules_block`). OpenCode follows the same path as every
# other platform.


def _inline_knowledge_md(root: Path, knowledge: list[dict[str, Any]]) -> str:
    """Render knowledge files/dirs as inlined fenced markdown blocks.

    OpenCode has no ``@file`` ref, so content must be inlined.  Each entry is
    preceded by a ``### file: <path>`` header so the source stays traceable.
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
class OpenCodeAdapter(AdapterBase):
    """Generate ``.opencode/`` config tree for OpenCode."""

    supports_file_ref = False
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
            f"# one-context — workspace `{ws_id}`",
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
                rel_path=f".opencode/instructions/onecxt-{ws_id}.md",
                content="\n".join(parts).rstrip() + "\n",
                description=f"OpenCode instructions for workspace {ws_id}",
                source_id=ws_id,
                source_kind="profile",
                target_kind="instructions",
                status="native",
                trigger="loaded via opencode.json `instructions` array",
                source_path=f"meta/workspaces.yaml#{ws_id}",
            ),
        ]

    def generate_agents(
        self,
        root: Path,
        agents: list,
        profiles_by_id: dict,
    ) -> list[GeneratedFile]:
        """Generate ``.opencode/agent/<id>.md`` for each agent.

        All one-context agents map to ``mode: subagent`` — invocable via ``@``
        without clobbering the built-in primary agents (build/plan).
        """
        import yaml

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
                body.append(f"Look for `{deploy_manifest}` in each repo root before deploying.")
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

            frontmatter = {
                "description": description or f"{name} (one-context agent)",
                "mode": "subagent",
            }
            fm_text = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
            content = "\n".join([
                "---",
                fm_text,
                "---",
                "",
                "\n".join(body).rstrip(),
                "",
            ])
            files.append(
                GeneratedFile(
                    rel_path=f".opencode/agent/{agent_id}.md",
                    content=content,
                    description=f"OpenCode agent config for {agent_id}",
                    source_id=agent_id,
                    source_kind="agent",
                    target_kind="agent",
                    status="native",
                    trigger="@<id> (subagent invocation; mode: subagent)",
                    source_path="meta/agents.yaml#" + agent_id,
                )
            )

        return files

    def generate_skills(
        self,
        root: Path,
        skills: list[SkillMeta],
    ) -> list[GeneratedFile]:
        """Generate ``.opencode/skills/<name>/SKILL.md`` thin shells.

        OpenCode discovers the shell through its existing project skill path;
        the shell asks the agent to read the canonical project source.
        """
        import yaml

        self._skills = list(skills)
        files: list[GeneratedFile] = []

        for skill in skills:
            fm = dict(skill.frontmatter)
            fm.setdefault("name", skill.name)
            if not fm.get("description"):
                first_line = next(
                    (ln.strip() for ln in skill.body.splitlines() if ln.strip()),
                    "",
                )
                fm["description"] = first_line.lstrip("# ").strip() or skill.name

            fm_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
            body = render_skill_reference_body(root, skill)
            content = "\n".join([
                "---",
                fm_text,
                "---",
                "",
                GENERATED_HEADER_MD,
                "",
                body,
                "",
            ])
            files.append(
                GeneratedFile(
                    rel_path=f".opencode/skills/{skill.dir_name}/SKILL.md",
                    content=content,
                    description=f"OpenCode skill registration for {skill.name}",
                    source_id=skill.dir_name,
                    source_kind="skill",
                    target_kind="skill",
                    status="native",
                    trigger="discovered via opencode.json skill path; shell reads canonical project source",
                    source_digest=source_digest((root / skill.source_path).read_text(encoding="utf-8")),
                    source_path=skill.source_path,
                )
            )

        return files

    def generate_project_artifacts(
        self,
        root: Path,
        workspace_ids: list[str],
        agents: list[dict[str, Any]],
    ) -> list[GeneratedFile]:
        """Generate ``.opencode/opencode.json`` wiring instructions + skills paths.

        Only schema-valid keys are emitted — OpenCode rejects unknown top-level
        keys with ``ConfigInvalidError``, so the GENERATED marker lives in the
        ``.md`` files, not here.
        """
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
        }
        if workspace_ids:
            config["instructions"] = [
                f".opencode/instructions/onecxt-{wid}.md" for wid in workspace_ids
            ]
            config["skills"] = {"paths": [".opencode/skills"]}

        files = [
            GeneratedFile(
                rel_path=".opencode/opencode.json",
                content=json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                description="OpenCode project config (instructions + skills paths)",
                source_id="opencode-project-config",
                source_kind="manifest",
                target_kind="manifest",
                status="aggregate",
                trigger="loaded by OpenCode at startup; deep-merged with global config",
                limitations=[
                    "no inline generated marker — OpenCode rejects unknown top-level keys; "
                    "USER_EDITED detection relies on adapt-report.json listing",
                    "instructions + skills paths aggregate all workspace + agent + skill entries",
                ],
                source_path="meta/workspaces.yaml",
            ),
        ]

        self._workspace_ids = []
        self._skills = []
        return files

    def unsupported_assets(self) -> list[ReportEntry]:
        """Return placeholder entries for asset kinds OpenCode supports but
        onecxt deliberately does not distribute (commands).

        Rules are intentionally NOT declared unsupported — they are
        distributed inline like for every other adapter.

        Per spec AC7 of ``features/core/adapter-compat-matrix/``: an explicit
        unsupported entry beats a silent skip — consumers of
        ``adapt-report.json`` get a complete capability matrix per target.
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
            for kind, why, fallback in _UNSUPPORTED_KINDS_BY_OPENCODE
        ]
