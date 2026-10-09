"""OpenClaw adapter — generates ``.openclaw/workspace-config.json``.

Consumer frequency: 2nd (Claude Code > OpenClaw > Codex > Cursor > Hermes).
Kept fully maintained. Per-agent configs are emitted as a registry
(``onecxt-project.json`` lists paths, not inlined content), so on-demand
agents impose no idle cost here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from one_context.adapters import AdapterBase, GeneratedFile, register
from one_context.adapters._rules import FieldRule, match_rules
from one_context.adapters._rules_ref import render_rules_block
from one_context.adapters._shared_rules import GENERATED_NOTICE_JSON, PROFILE_RULES


def _collect_instructions(profiles: list[dict[str, Any]]) -> list[str]:
    """Collect all matching rule outputs across profiles."""
    instructions: list[str] = []
    for profile in profiles:
        matched = match_rules(profile, PROFILE_RULES)
        for rule in matched:
            instructions.append(rule.output)
    return instructions


def _inline_knowledge_text(root: Path, knowledge: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Read knowledge files and return as structured entries."""
    entries: list[dict[str, str]] = []
    for item in knowledge:
        if not item.get("exists"):
            continue
        abs_path = Path(item["absolute_path"])
        if abs_path.is_file():
            content = abs_path.read_text(encoding="utf-8").rstrip()
            entries.append({"source": item["path"], "content": content})
        elif abs_path.is_dir():
            for child in sorted(abs_path.rglob("*.md")):
                child_rel = child.relative_to(root).as_posix()
                content = child.read_text(encoding="utf-8").rstrip()
                entries.append({"source": child_rel, "content": content})
    return entries


@register("openclaw")
class OpenClawAdapter(AdapterBase):
    """Generate ``.openclaw/onecxt-{ws}.json`` workspace instructions.

    Scope (verified against OpenClaw 2026.3.x): OpenClaw does **not**
    auto-discover project-level ``.openclaw/`` directories. This adapter
    only emits:
      * ``.openclaw/onecxt-<ws>.json``       — per-workspace instructions
        (incl. inlined ``rules_text`` from ``.onecxt/RULES.md``)
      * ``.openclaw/onecxt-project.json``    — workspace registry only

    Per-skill JSON (``.openclaw/skills/<name>.json``) and per-agent JSON
    (``.openclaw/agents/<id>.json``) are **deliberately not generated**:
    OpenClaw doesn't read them; install globally with
    ``openclaw skills install`` / ``openclaw agents add`` if needed.
    """

    supports_file_ref = False

    def __init__(self) -> None:
        # No state to retain across phases: we don't collect skills/agents.
        pass

    def generate(
        self,
        root: Path,
        workspace: dict[str, Any],
        context: dict[str, Any],
    ) -> list[GeneratedFile]:
        ws_id = workspace.get("id", "unknown")
        ws_ctx = workspace.get("context") or {}

        config: dict[str, Any] = {
            "_generated": GENERATED_NOTICE_JSON,
            "workspace_id": ws_id,
            "name": workspace.get("name", ""),
            "description": workspace.get("description", ""),
        }

        # Context / focus
        if ws_ctx.get("summary"):
            config["summary"] = ws_ctx["summary"]
        if ws_ctx.get("focus"):
            config["focus"] = ws_ctx["focus"]

        # Profile instructions
        profiles = context.get("profiles") or []
        instructions = _collect_instructions(profiles)
        if instructions:
            config["instructions"] = instructions

        # Profile metadata
        if profiles:
            config["profiles"] = [
                {
                    "id": p.get("id", ""),
                    "name": p.get("name", ""),
                    "mode": p.get("mode", ""),
                }
                for p in profiles
            ]

        # Knowledge — inline
        knowledge = context.get("knowledge") or []
        knowledge_entries = _inline_knowledge_text(root, knowledge)
        if knowledge_entries:
            config["knowledge"] = knowledge_entries

        # Project Rules — inlined full text from .onecxt/RULES.md.
        # JSON stores the rules body as a single string field so the
        # consumer's LLM gets the entire constraint surface at startup.
        rules_block = render_rules_block(root)
        if rules_block:
            # Strip the "## Project Rules\n\n" prefix that the markdown
            # adapters expect — for JSON we keep only the body.
            lines = rules_block.splitlines()
            # Drop the leading "## Project Rules[ — ...]" line + blank line.
            while lines and (
                lines[0].startswith("## Project Rules")
                or lines[0].strip() == ""
            ):
                lines.pop(0)
            config["rules_text"] = "\n".join(lines).strip()

        content = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
        return [
            GeneratedFile(
                rel_path=f".openclaw/onecxt-{ws_id}.json",
                content=content,
                description=f"OpenClaw config for workspace {ws_id}",
                source_id=ws_id,
                source_kind="profile",
                target_kind="instructions",
                status="native",
                trigger="loaded by OpenClaw from .openclaw/onecxt-project.json workspaces[].path",
                source_path=f"meta/workspaces.yaml#{ws_id}",
            ),
        ]

    def generate_project_artifacts(
                self,
                root: Path,
                workspace_ids: list[str],
                agents: list[dict[str, Any]],
            ) -> list[GeneratedFile]:
                """Generate ``.openclaw/onecxt-project.json`` workspace registry.

                Scope (verified against OpenClaw 2026.3.x):
                OpenClaw does **not** auto-load project-level ``.openclaw/skills/``
                or ``.openclaw/agents/`` directories. Skills live at
                ``~/.openclaw/skills/`` + ``~/.agents/skills/`` (both global,
                managed by ``openclaw skills`` CLI), and agents are defined in
                ``~/.openclaw/agents/<id>/`` global config. Therefore this
                manifest only registers workspace-level instructions files; we
                intentionally do **not** emit per-skill or per-agent JSON files
                that OpenClaw would never read.

                See ``knowledge/01_Compiled/tooling/agent-cli-skill-loading-mechanisms.md``
                for the per-platform skill-loading fact table.
                """
                del root, agents
                payload: dict[str, Any] = {
                    "_generated": GENERATED_NOTICE_JSON,
                    "one_context": {
                        "version": 1,
                        "description": (
                            "OpenClaw workspace registry — generated by `onecxt adapt`."
                        ),
                        "workspaces": [
                            {"id": wid, "path": f".openclaw/onecxt-{wid}.json"}
                            for wid in workspace_ids
                        ],
                        # Deliberately no `agents` or `skills` sections: OpenClaw
                        # does not read project-level .openclaw/agents/<id>.json or
                        # .openclaw/skills/<name>.json. To make a skill or agent
                        # available to OpenClaw, install it globally:
                        #   openclaw skills install <path>
                        #   openclaw agents add <id>
                    },
                }
                content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

                return [
                    GeneratedFile(
                        rel_path=".openclaw/onecxt-project.json",
                        content=content,
                        description="OpenClaw workspace registry (workspace-level instructions only)",
                        source_id="openclaw-project-manifest",
                        source_kind="manifest",
                        target_kind="manifest",
                        status="aggregate",
                        trigger=(
                            "OpenClaw does not auto-discover project-level .openclaw/; "
                            "this manifest is a portable backup. Skills/agents must be "
                            "installed globally via `openclaw skills install` etc."
                        ),
                        limitations=[
                            "OpenClaw does not read project-level .openclaw/ at startup",
                            "no per-skill or per-agent JSON generated — would be dead files",
                        ],
                        source_path="meta/workspaces.yaml",
                    ),
                ]
