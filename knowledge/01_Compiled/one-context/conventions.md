# one-context Workspace Conventions

Working conventions for changes made under this one-context root. These are
engineering conventions, not tool instructions; adapters surface them to AI
tools, humans apply them everywhere.

## Naming and identifiers

- Manifest ids (`repos`, `workspaces`, `agents`, `skills`) are one portable
  lowercase path component: ASCII letters, digits, `.`, `_`, `-`. Prefer
  kebab-case for features, snake-free ids (e.g. `dev`, `my-api`).
- Feature ids are kebab-case and match their directory:
  `features/<category>/<feature-id>/`.

## Change flow

1. Write the spec from `features/_template/` and register it in
   `features/INDEX.md`.
2. Add a tech design for non-trivial changes; skip with a note in the spec
   for trivial ones.
3. Implement in the repository that owns the behavior; write targeted tests.
4. Produce `test_report.md`, then `deliver.md` summarizing the release note.
5. Run `onecxt doctor` and `onecxt adapt --check` before opening a PR.

## Knowledge and rules

- `.onecxt/RULES.md` is the single constraint surface; keep it imperative
  and short. Tool-specific wording belongs in the adapters, not here.
- Canonical knowledge lives under `knowledge/01_Compiled/one-context/`;
  reference files with root-relative `@` paths.
- Personal or sensitive notes either use `visibility: private` frontmatter
  (excluded from automated collection, still on disk) or live outside
  tracked paths entirely.

## Repositories and worktrees

- Cloned repositories live under `repos/` (git-ignored); register them in
  `meta/repos.yaml` so `onecxt sync` and context exports can find them.
- Feature branches follow the dev agent's `worktree.branch_pattern`
  (`feature/{feature_id}` in this sample) and worktrees live under
  `.worktrees/{repo_id}/{feature_id}` (git-ignored).

## Generated surfaces

- Anything carrying the generated marker is a build output of `onecxt adapt`
  — regenerate, never hand-edit, and commit the regenerated files together
  with the SoT change that caused them.
- `adapt-report.json` describes the compatibility matrix of the last run;
  treat it as diagnostic output.
