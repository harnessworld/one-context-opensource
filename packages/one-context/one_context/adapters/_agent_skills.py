"""Shared Agent Skills projection for Codex and Pi.

Both tools natively discover project skills from ``.agents/skills``.  The
projection is generated once by the adapter orchestrator so a full adapt run
does not create duplicate producers for the same path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from one_context.adapters import GeneratedFile
from one_context.adapters._report import source_digest
from one_context.adapters._shared_rules import GENERATED_HEADER_MD
from one_context.skills import SkillMeta, render_skill_reference_body

_DEFAULT_ALLOWED_TOOLS = "Read, Write, Edit, Bash, Grep, Glob"


def generate_shared_agent_skills(
    root: Path,
    skills: list[SkillMeta],
    consumers: tuple[str, ...] = ("codex", "pi"),
) -> list[GeneratedFile]:
    """Generate one project-local thin shell for each canonical skill."""
    consumer_label = "/".join(name.title() for name in consumers)
    trigger = f"auto-discovered by {consumer_label}"
    limitations: tuple[str, ...] = ()
    if "pi" in consumers:
        trigger += " after Pi project trust"
        limitations = (
            "Pi requires the project to be trusted before loading "
            "project-local skills",
        )
    trigger += "; shell reads the canonical project source"

    files: list[GeneratedFile] = []
    for skill in skills:
        frontmatter = dict(skill.frontmatter)
        # Cursor filters other surfaces; Codex/Pi keep their native discovery.
        frontmatter["metadata"] = {
            **(frontmatter.get("metadata") or {}),
            "surfaces": ",".join(consumers),
        }
        frontmatter.setdefault("name", skill.name)
        if not frontmatter.get("description"):
            first_line = next(
                (line.strip() for line in skill.body.splitlines() if line.strip()),
                "",
            )
            frontmatter["description"] = (
                first_line.lstrip("# ").strip() or skill.name
            )
        # Codex understands this field; Pi accepts it as optional Agent Skills
        # metadata and remains lenient about tool-name conventions.
        frontmatter.setdefault("allowed-tools", _DEFAULT_ALLOWED_TOOLS)

        frontmatter_text = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        content = "\n".join([
            "---",
            frontmatter_text,
            "---",
            "",
            GENERATED_HEADER_MD,
            "",
            render_skill_reference_body(root, skill),
            "",
        ])
        files.append(
            GeneratedFile(
                rel_path=f".agents/skills/{skill.dir_name}/SKILL.md",
                content=content,
                description=(
                    f"Shared {consumer_label} project skill shell for {skill.name}"
                ),
                source_id=skill.dir_name,
                source_kind="skill",
                target_kind="skill",
                status="native",
                trigger=trigger,
                limitations=limitations,
                source_digest=source_digest(
                    (root / skill.source_path).read_text(encoding="utf-8")
                ),
                source_path=skill.source_path,
                targets=consumers,
            )
        )
    return files
