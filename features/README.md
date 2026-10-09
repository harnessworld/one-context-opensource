# features/ — spec-driven change flow

Every non-trivial change in this workspace flows through a *feature*:
a small set of Markdown artifacts that keep requirements, design, tests, and
delivery in one place — readable by humans and AI tools alike.

## Directory layout

```text
features/
├── README.md                 ← this file
├── INDEX.md                  ← one row per feature (enforced by doctor)
├── _template/                ← copy this directory to start a feature
│   ├── spec.md
│   ├── tech_design.md
│   ├── test_report.md
│   └── deliver.md
└── <category>/<feature-id>/  ← real features, e.g. core/my-feature/
    └── ... same four files (+ optional worktrees.yaml)
```

- `_template/` and directories starting with `_` are tooling, not features:
  doctor ignores them when checking index closure.
- Feature ids are kebab-case and must match their directory name.
- `onecxt worktree setup <feature-id>` uses the dev agent's patterns to
  create branches/worktrees once repositories are registered.

## Workflow

1. **spec.md** — the PM agent's output: goals, scope, acceptance criteria.
2. **tech_design.md** — the architect's output for non-trivial changes.
3. Implementation happens in the repositories that own the behavior.
4. **test_report.md** — the QA agent's output against the acceptance criteria.
5. **deliver.md** — delivery note: what shipped, how to verify, follow-ups.
6. Register the feature in `INDEX.md` and run `onecxt doctor`.

## Index contract

`INDEX.md` is a Markdown table; each feature appears exactly once.
`onecxt doctor` fails on:

- duplicate ids or paths in the index,
- an indexed path without a `spec.md`,
- a `spec.md` missing from the index.

An empty index (a table with only headers) is valid — that is the state
this sample ships in.
