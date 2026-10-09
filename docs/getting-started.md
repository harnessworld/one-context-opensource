# Getting started

This guide takes a fresh clone of this repository to a working one-context
workspace: install, validate, generate tool surfaces, and export context.

> **Status:** beta. The Python package requires **Python 3.10 or newer**
> (CI currently exercises 3.10 and 3.12 on Linux, plus 3.12 on macOS and
> Windows). Manifest formats may still evolve between releases — see
> [`CHANGELOG.md`](../CHANGELOG.md).

## 1. Clone and create a virtual environment

```bash
git clone https://github.com/harnessworld/one-context-opensource.git one-context
cd one-context
```

POSIX (Linux / macOS):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Windows (PowerShell):

```powershell
# If the `python` command is not on PATH, create the venv with the
# launcher instead:  py -m venv .venv
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

After the venv is activated, always use `python -m pip ...` (not bare
`py -m pip`): `py` can resolve to a *different* interpreter than the
activated venv. Use `py` only to create the venv.

The venv lives in ignored paths (`.venv*`); pick any name you like.

## 2. Install the CLI (editable)

```bash
python -m pip install -e "./packages/one-context[dev]"
onecxt --version
```

The `[dev]` extra adds test tooling; the base install needs only `PyYAML`
and `pydantic`. If the `onecxt` entry point is not on your
`PATH`, use `python -m one_context` — every subcommand is identical.

## 3. Validate the sample workspace

```bash
onecxt doctor
```

The repository ships with an empty repository registry, one `dev`
workspace, seven generic agent roles, canonical knowledge, and one example
skill — enough for `doctor` and `adapt` to run without any external
repository. Expect a clean pass (warnings may appear for missing local
repos only after you register some).

## 4. Generate tool surfaces

```bash
onecxt adapt dev
```

This regenerates every supported tool's native configuration from
`meta/*.yaml`, `.onecxt/RULES.md`, `knowledge/`, and `skills/` — and the
tool-neutral root `AGENTS.md`. The `cursor` adapter is skipped by default
(its always-on rules would inflate every chat); opt in with:

```bash
onecxt adapt dev --only cursor
```

Other useful variants:

```bash
onecxt adapt --all                 # every workspace in meta/workspaces.yaml
onecxt adapt dev --only pi         # single adapter
onecxt adapt dev --dry-run         # preview without writing
onecxt adapt dev --check           # verify generated files match SoT
```

To keep generated surfaces fresh automatically across pulls and checkouts:

```bash
onecxt adapt-install
```

## 5. Export a context bundle

```bash
onecxt context export dev                       # JSON to stdout
onecxt context export dev --format markdown     # Markdown variant
onecxt context export dev --format markdown --output context-dev.md
onecxt context export dev --compress --target-tokens 8000
```

The export is tool-neutral: repositories, resolved profiles, and expanded
knowledge paths for the selected workspace. It works before any repository
is registered (counts will simply be zero).

## 6. Add your own repositories

Edit `meta/repos.yaml`:

```yaml
repos:
  - id: my-api
    url: https://example.com/your-org/my-api.git
    category: develop
    description: Example service
```

Then:

```bash
onecxt sync               # clone missing repos under repos/, fast-forward the rest
onecxt repo list
```

Clones land in `repos/` (git-ignored). To include a repo in a workspace,
list its id explicitly under the workspace's `repos` key:

```yaml
workspaces:
  - id: dev
    repos:
      - my-api
```

Repos are always opt-in: omitting `repos` means the workspace starts with
zero repositories.

## 7. Add your own knowledge

Drop Markdown files under `knowledge/` and reference them from
`meta/workspaces.yaml` (`context.knowledge`) or `meta/agents.yaml`
(`knowledge`). Directory entries expand to every contained `.md`. Files
with `visibility: private` frontmatter are excluded from context exports
and adapter projections (see [`knowledge/README.md`](../knowledge/README.md)). The canonical
overview and conventions live in `knowledge/01_Compiled/one-context/`.

If you prefer to keep knowledge in a separate repository, you can later
replace `knowledge/` with your own submodule; this sample repo has no
submodule assumptions.

## 8. Add your own features and skills

- **Features:** copy `features/_template/` to
  `features/<category>/<feature-id>/`, fill in the four artifacts, add an
  `INDEX.md` row, and run `onecxt doctor` — it enforces index closure.
- **Skills:** create `skills/<name>/SKILL.md` (see
  [`skills/README.md`](../skills/README.md)) and rerun `onecxt adapt`.
  The example `workspace-review` skill only reads manifests via
  `onecxt doctor` and is safe to keep or delete.

## Where to go next

- [`docs/architecture.md`](architecture.md) — how the pieces fit together
- [`docs/configuration.md`](configuration.md) — manifest schema reference
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — development workflow
- [`SECURITY.md`](../SECURITY.md) — vulnerability reporting
