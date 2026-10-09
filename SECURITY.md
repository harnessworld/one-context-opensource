# Security Policy

Use private reporting for vulnerabilities that could expose workspace data or
allow unintended file changes.

## Supported versions

| Version | Supported |
|---------|-----------|
| Unreleased `main` | Best-effort fixes; this is currently the only line |

Until the first stable release there is no LTS track. Security issues are
fixed on `main` and documented in `CHANGELOG.md`.

## Reporting a vulnerability

Please do **not** open a public issue for suspected vulnerabilities.

1. Use GitHub's **private vulnerability reporting** feature on this
   repository (Security → Advisories → "Report a vulnerability"), or
2. If that is unavailable, contact a repository maintainer directly using
   the contact information shown on their GitHub profile.

Include affected commands, a minimal reproduction, and the impact you observed.
Maintainers will coordinate fixes and disclosure privately; this beta project
does not offer a guaranteed response time.

## Scope and threat model (what this project actually does)

- one-context reads local manifests and Markdown, generates local
  configuration files, and — via `sync` / `smart-commit` / `worktree` —
  runs git commands against repositories you register. Core workspace commands run locally; repository operations use Git
  authentication. Optional skill evaluation invokes a model provider or an
  installed agent CLI and may send the selected evaluation context externally.
- `onecxt skills install` copies skill bundles into user home directories
  (`~/.claude/skills`, `~/.codex/skills`) with filename- and
  content-based filters for obvious secret material. This is a hygiene
  filter, not a security boundary.
- Private knowledge frontmatter (`visibility: private`) is a collection
  filter only: such files are excluded from context exports and adapter
  projections, but they remain readable on disk and in git history if
  committed. Do not treat it as encryption or access control.

## Known risk areas to keep in mind

- **Model context injection.** Generated files are injected verbatim into
  AI tool contexts. Treat `.onecxt/RULES.md`, knowledge files, and skill
  bodies with the same care as any executable configuration: review
  changes to them the way you would review code. Untrusted content merged
  into these files can steer AI assistants.
- **Git hooks.** `onecxt adapt-install` writes hooks into the local
  repository's `.git/hooks`. Inspect generated hooks before enabling them
  on machines with sensitive credentials.
- **Push automation.** `smart-commit` defaults to plan-only; execution
  requires `--yes`. Passing `--plan-id` additionally pins execution to the
  reviewed plan; it is optional. Do not wire
  `smart-commit --yes --push` into unattended automation without review.
