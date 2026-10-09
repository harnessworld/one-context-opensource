## Summary

One or two sentences: what changed and why.

Link the related issue: Fixes #<issue number> (or `N/A`).

## Checklist

- [ ] I ran `pytest` under `packages/one-context/` and it passes.
- [ ] I ran `onecxt doctor` and it passes (errors fixed, warnings triaged).
- [ ] For manifest/knowledge/skill/rules changes: `onecxt adapt dev` was
      rerun so generated surfaces stayed in sync, and
      `onecxt adapt dev --check` passes.
- [ ] New or changed manifests are covered or validated by doctor checks.
- [ ] No secrets, private knowledge content, or machine-specific routes in
      the diff (`.env`, `.onecxt/local.yaml` stay untracked).
- [ ] Docs (`README.md`, `README.zh-CN.md`, `docs/**`) updated where the
      change affects setup, commands, or schemas.

## Notes for reviewers

Anything non-obvious: design decisions, rejected alternatives, follow-ups.
