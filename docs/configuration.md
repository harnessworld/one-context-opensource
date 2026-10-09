# Configuration reference

All canonical configuration is hand-written, tracked, and validated by
`onecxt doctor`. Generated files (see "Generated state" below) are derived
from these sources only.

## Root discovery

`onecxt` finds the workspace root in this order:

1. `--root PATH` on the command line
2. `ONECXT_ROOT` environment variable
3. Walking up from the current directory

A directory qualifies as a root when `meta/repos.yaml` **or**
`meta/agents.yaml` exists. This sample ships both.

## `meta/repos.yaml` — repository registry

```yaml
repos:
  - id: my-api                # optional; defaults to repo name from URL
    url: https://example.com/your-org/my-api.git   # required
    category: develop         # required when 'path' omitted
    path: repos/develop/my-api  # optional; must stay inside the root
    alias: api                # optional; string or list of strings
    aliases: [service]        # optional; merged with alias
    description: free text    # optional
```

- `onecxt sync` clones missing entries under the root and fast-forwards
  existing clones; `onecxt sync <id-or-alias>` narrows the selection.
- Ids and aliases are unique case-insensitively.
- An empty registry (`repos: []`) is valid — doctor, adapt, and export all
  run without any repository present.

## `meta/workspaces.yaml` — workspaces

```yaml
workspaces:
  - id: dev                   # required, one portable lowercase path component
    name: Development Workspace
    description: free text
    repos:                    # optional; omitted means zero repos (opt-in)
      - my-api
    profiles:                 # optional; ids must exist in profiles.yaml
      - default-coding
    context:
      summary: free text      # inlined into workspace instructions
      focus:                  # optional bullet list
        - item
      knowledge:              # files or directories, root-relative
        - knowledge/01_Compiled/one-context/project-overview.md
        - knowledge/01_Compiled/one-context/
```

- Directory entries in `context.knowledge` expand to every contained
  `.md`, skipping files marked `visibility: private`.
- `repos` is opt-in: only explicitly listed ids are included in the
  workspace context; omitting the key means zero repositories.
- `onecxt workspace show <id>` prints the resolved definition (JSON);
  `onecxt context export <id>` emits the full bundle
  (`--format json|markdown`, `--compress`, `--target-tokens N`,
  `--output PATH`).

## `meta/agents.yaml` — agent roles

```yaml
agents:
  - id: dev                   # required; becomes /agent-dev in Pi
    name: Developer
    role: dev                 # required; see below
    profile: default-coding   # optional; must exist in profiles.yaml
    description: when to invoke
    instructions: |
      free text
    knowledge:                # optional files/dirs per role
      - knowledge/01_Compiled/one-context/conventions.md
    owns:                     # optional artifact list (free text)
      - features/**/spec.md
    deploy_manifest: deploy.yaml   # optional, only meaningful for role=sre
    worktree:                 # optional, only intended for role=dev
      branch_pattern: "feature/{feature_id}"
      path_pattern: ".worktrees/{repo_id}/{feature_id}"
      base_branch: main
```

- Valid roles: `pm`, `architect`, `dev`, `qa`, `sre`, `reviewer`,
  `knowledge-keeper` (validated by doctor).
- `worktree` requires both placeholders in `path_pattern`
  (`{repo_id}` and `{feature_id}`) and `{feature_id}` in
  `branch_pattern`; patterns generate paths used by `onecxt worktree`.
- Agent ids are one portable lowercase path component and unique
  case-insensitively.

## `meta/profiles.yaml` — shared behavior profiles

Already part of this sample (see the file for the canonical set). Concepts:

```yaml
version: 1
mixins:
  - id: strict-review      # reusable behavior fragments
profiles:
  - id: default-coding     # base profiles: no extends
  - id: backend-coding
    extends: default-coding  # single-layer inheritance only
    mixins: [output-concise] # applied after parent, before self
```

- Merge order: parent (`extends`) → mixins in declared order → own fields.
  Dicts deep-merge; lists and scalars replace.
- Constraints enforced by doctor: no multi-layer inheritance, parents carry
  no mixins, mixins define neither `extends` nor `mixins`.
- Inspect with `onecxt profile list` and
  `onecxt profile show <id> --resolved`.

## `.onecxt/RULES.md` — constraint surface

One Markdown file, inlined verbatim (after its leading title) into every
adapter's instructions artifact under a Project Rules section. Missing or
empty file is surfaced by doctor as a warning. Keep it imperative and tool
neutral.

## `.env`

`onecxt doctor` and `onecxt sync` load `.env` at the root if present
(simple `KEY=VALUE` lines; never overrides existing environment
variables). `.env` and `.env.*` files are git-ignored.

## `.onecxt/local.yaml` — per-machine settings (ignored)

Machine-local defaults consumed by `onecxt smart-commit` (transport,
proxy, timeouts, attribution). Never commit it; it is git-ignored by
default. An optional `.onecxt/sensitive-words.yaml` customizes the commit
safety scan.

## `deploy.yaml` — per-repo deploy manifest (optional)

Validated by doctor when present in a registered repository root:

```yaml
version: "1"
name: my-service
strategy: docker-compose   # docker-compose | helm | raw-script | manual | none
stages:
  - id: build
    cmd: make build
  - id: roll
    cmd: make rollout
    health_check: curl -fsS localhost:8080/health
rollback:
  cmd: make rollback
```

Doctor warns when no stage defines `health_check` or an active strategy
lacks `rollback.cmd`.

## Skills — `skills/<name>/SKILL.md`

Frontmatter keys recognized everywhere: `name`, `description`.
`metadata` passes through. Additional keys (e.g. `aliases`, used by the Pi
adapter for `/<alias>` prompts) are kept in the canonical file and stripped
from Codex copies. Skill names must be one portable path component.

## Knowledge privacy frontmatter

Any `.md` under `knowledge/` with:

```yaml
---
visibility: private
---
```

is excluded from context exports and adapter projections (knowledge
expansion into generated instructions). This is a collection filter, not an
access-control or git mechanism.

## Generated state (do not hand-edit)

| Path | Producer |
|---|---|
| `AGENTS.md` | `onecxt adapt` (tool-neutral; regenerated from overview + rules) |
| `CLAUDE.md`, `.claude/**` | `claude_code` adapter |
| `.codex/**` | `codex` adapter |
| `.cursor/**` | `cursor` adapter (opt-in) |
| `.hermes/**`, `.hermes.md` | `hermes` adapter |
| `.openclaw/**` | `openclaw` adapter |
| `.opencode/**` | `opencode` adapter |
| `.pi/**`, `.agents/skills/**` | `pi` / shared skills output |
| `adapt-report.json` | last `adapt` run (compat matrix, probes) |
| `.onecxt/adapt-cache.json` | incremental adapt cache |

All of these stay outside git except when a maintainer decides to commit
specific surfaces (the root `AGENTS.md` is normally committed so tools that
read it work before generation). `adapt --check` keeps everything honest.

## Feature templates — `features/_template/`

`spec.md`, `tech_design.md`, `test_report.md`, `deliver.md`. Copy to
`features/<category>/<feature-id>/`, register in `features/INDEX.md`;
doctor enforces one index row per spec outside `_template/`.
