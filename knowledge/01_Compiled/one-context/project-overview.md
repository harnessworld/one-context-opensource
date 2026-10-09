# one-context — Project Overview

This document is the canonical project overview. `onecxt adapt` inlines the
`##` sections below into every tool's startup context, including the
generated root `AGENTS.md`. Keep it factual and short; edit this file (not
the generated mirrors) when the picture changes.

## What this project is

one-context is a workspace control plane that keeps many repositories, many
agents, and many AI coding tools aligned on one shared context. Canonical
intent is written once — in `meta/*.yaml`, `.onecxt/RULES.md`,
`knowledge/`, and `skills/` — and then generated into each tool's native
format by `onecxt adapt`.

## Core building blocks

- **meta/ manifests** — declarative registries: repositories
  (`meta/repos.yaml`), workspaces (`meta/workspaces.yaml`), agents
  (`meta/agents.yaml`), and shared behavior profiles
  (`meta/profiles.yaml`).
- **.onecxt/RULES.md** — the single constraint surface, inlined verbatim
  into every adapter's startup context.
- **knowledge/** — canonical notes and conventions. Files or directories
  referenced from manifests are expanded into exports; files carrying
  `visibility: private` frontmatter are excluded from collection.
- **skills/** — reusable agent skills. Each skill is one directory with a
  `SKILL.md` (YAML frontmatter + instructions); adapters publish them to
  each tool's native skill surface.
- **features/** — spec-driven change flow (spec → tech design → test
  report → delivery note) with an index enforced by `onecxt doctor`.
- **Adapters** — per-tool generators included in `onecxt adapt`:
  `claude_code`, `codex`, `cursor` (opt-in via `--only cursor`), `hermes`,
  `openclaw`, `opencode`, `pi`. Cursor is skipped by default because its
  always-on rules would inflate every chat.

## How it behaves

- `onecxt doctor` cross-checks manifests, knowledge references, feature
  index closure, and rules presence; it is warnings/errors only — it never
  mutates files.
- `onecxt adapt <workspace>` regenerates generated surfaces and refuses to
  overwrite files that no longer carry the generated marker (protecting
  hand edits); `adapt --check` verifies the tree is in sync.
- `onecxt context export <workspace>` emits a tool-neutral bundle (JSON or
  Markdown) of repos, profiles, and knowledge for any consumer.

## Conventions

See @knowledge/01_Compiled/one-context/conventions.md for the working
conventions used across this workspace.
