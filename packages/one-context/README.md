# one-context Python package

The `onecxt` command and importable `one_context` module provide the core of
[one-context](../../README.md): registry validation, repository synchronization,
context export and native AI tool configuration.

## Installation

From the repository root, inside a Python 3.10+ virtual environment:

```bash
python -m pip install -e "./packages/one-context[dev]"
python -m one_context --help
```

The repository checkout includes a minimal workspace. No submodule initialization
or credentials are needed for its local validation and adaptation commands.

```bash
onecxt doctor
onecxt adapt dev
onecxt adapt dev --only cursor
onecxt context export dev --format markdown
```

`onecxt` and `python -m one_context` expose the same commands. Use the global
`--root PATH` option or `ONECXT_ROOT` to select a workspace from another directory.

## Development

```bash
cd packages/one-context
python -m pytest tests/ -q
```

Tests marked `slow` are excluded by default. Opting in may call a model provider
and consume tokens; inspect the required environment before running them.

See the [getting started guide](../../docs/getting-started.md),
[architecture](../../docs/architecture.md) and
[contribution guide](../../CONTRIBUTING.md).

## Advanced commands

The CLI also includes worktree management, skill installation, skill evaluation
and a plan-first smart-commit command. Inspect each command with `--help` before
using operations that write files, call providers or push repositories. Sync and
push require the appropriate Git access; evaluation may require provider credentials.

Machine-specific defaults belong in ignored `.onecxt/local.yaml`, not shared
manifests. The default fallback target branch for smart-commit is `main`; an
explicit branch configuration or attached current branch takes precedence.
