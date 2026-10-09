"""Project-level rules inline block (shared across adapters).

The canonical project rules live in a single file: ``.onecxt/RULES.md``.

Each adapter's generated instructions artifact (``AGENTS.md``, ``CLAUDE.md``,
``.opencode/instructions/``, ``.codex/instructions/``, ``.cursor/rules/*.mdc``,
``.hermes/onecxt-*.md``, ``.openclaw/onecxt-*.json``) appends the full text
of ``RULES.md`` under a ``## Project Rules`` section.

Why **inline** (not ``@file``, not path-only):

Earlier versions tried ``@rules/<topic>.md`` references. Two failure modes:

* ``@file`` is only auto-expanded by a few tools (Claude Code does; Codex
  / Cursor / OpenCode's AGENTS.md parser don't), so behaviour fractured
  per-target.
* A bare path (``rules/git.md``) relies on the LLM *noticing* it should
  Read the file — but skills are pulled on-demand (loss is "skill fails to
  trigger"), whereas **rules are constraints** (loss is "LLM violates the
  constraint"). Skills can be lazy; rules must be eager.

So: inline the whole ``.onecxt/RULES.md`` into every target's startup
context. Single SoT (one file in repo), N copies at runtime. ``onecxt
adapt-install`` ships a pre-commit hook to keep the N copies aligned
automatically.

Historical note: this module used to scan ``rules/`` for per-topic files.
The single-file design replaced it on 2026-07-30 — easier for users to
maintain, easier for the framework to keep in sync.
"""

from __future__ import annotations

from pathlib import Path

_RULES_FILE = Path(".onecxt/RULES.md")

# Default heading used when the file has no H1 of its own.
_DEFAULT_HEADING = "## Project Rules"


def _strip_leading_html_comments(text: str) -> str:
    """Drop leading ``<!-- ... -->`` / blank lines so the inlined section
    starts cleanly at the file's own content.

    Internal markers like ``<!-- INTERNAL ... -->`` at the top are not meant
    for the generated output.
    """
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("<!--") or stripped == "":
            start = i + 1
            continue
        break
    return "\n".join(lines[start:]).strip()


def render_rules_block(root: Path) -> str:
    """Return the ``## Project Rules`` section with the full RULES.md body.

    Returns ``""`` when ``.onecxt/RULES.md`` is missing — so adapters
    don't emit an empty section in fresh-clone state before rules exist.
    """
    rules_path = root / _RULES_FILE
    if not rules_path.is_file():
        return ""
    text = rules_path.read_text(encoding="utf-8").rstrip()
    body = _strip_leading_html_comments(text)

    # If the file already opens with an H1 (# Project Rules — …) we use it
    # verbatim; otherwise we wrap under our canonical heading so the
    # inlined block always starts with a section header at the same depth
    # the callers expect.
    if body.startswith("# "):
        # Promote the existing H1 to H2 so it nests cleanly inside the
        # generated CLAUDE.md / AGENTS.md / instructions sections. The
        # file author documents the project at H1 inside .onecxt/RULES.md;
        # we keep the title's intent but downgrade a level.
        first_nl = body.find("\n")
        title = body[:first_nl].lstrip("# ").strip() if first_nl != -1 else body.lstrip("# ").strip()
        rest = body[first_nl + 1 :].strip() if first_nl != -1 else ""
        if not title:
            return f"{_DEFAULT_HEADING}\n\n{body}\n"
        # If the file's own H1 already carries the "Project Rules" name
        # (e.g. "# Project Rules — one-context"), demote it verbatim —
        # prepending the default heading again would duplicate it.
        default_title = _DEFAULT_HEADING.lstrip("# ").strip()
        if title.lower().startswith(default_title.lower()):
            return f"## {title}\n\n{rest}\n" if rest else f"## {title}\n"
        return f"{_DEFAULT_HEADING} — {title}\n\n{rest}\n"

    return f"{_DEFAULT_HEADING}\n\n{body}\n"


def rules_source_path() -> str:
    """Return the repo-relative path of the SoT rules file.

    Used by ``onecxt doctor`` to surface the canonical location, and by
    the pre-commit hook to decide whether to re-run ``onecxt adapt``
    before allowing a commit touching RULES.md through.
    """
    return _RULES_FILE.as_posix()