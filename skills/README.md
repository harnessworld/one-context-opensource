# skills/ — reusable agent skills

A *skill* is one directory containing a `SKILL.md`: YAML frontmatter plus
the instructions an AI tool should follow when the skill is invoked. Skills
are discovered by the framework and published to each tool's native skill
surface by `onecxt adapt`.

## Ownership

- This directory is an ordinary directory of fresh framework examples — no
  submodule assumptions are built in.
- You may keep examples, extend them, or replace this directory entirely
  with your own collection (including as your own git submodule, if you
  prefer to version skills separately). one-context only requires that every
  skill lives at `skills/<name>/SKILL.md`.
- Skill names must be one portable path component (letters, digits, `.`,
  `_`, `-`). Directories without a `SKILL.md` are ignored.

## Example layout

```text
skills/
├── README.md
└── workspace-review/          ← one harmless example skill
    └── SKILL.md
```

## SKILL.md shape

```markdown
---
name: my-skill
description: One-line summary shown in the tool's skill menu.
---

Instructions for the AI tool. Plain Markdown; keep it imperative and short.
```

Notes:

- Codex only recognises the `name`, `description`, and `metadata`
  frontmatter keys; extra keys (e.g. `aliases` for Pi short-name prompts)
  are stripped from Codex copies while the canonical file keeps them.
- The `skills/` tree may also carry optional resource subdirectories
  (`references/`, `playbooks/`, `scripts/`, `assets/`, `agents/`) that
  runtime installs copy alongside the skill. Keep skills small; nothing
  secret ever belongs in a skill bundle.

## Runtime install (optional)

For user-scope installs into a tool runtime rather than project surfaces:

```bash
onecxt skills list --tool claude
onecxt skills install workspace-review --tool claude
onecxt skills uninstall workspace-review --tool claude
```

## Authoring guardrails

- Skills must stay self-contained and read-only by default; a skill that
  needs to change state should say so explicitly.
- Do not embed credentials, absolute paths, machine-specific routes, or
  provider endpoints in a skill.
