#!/usr/bin/env python3
"""cc PostToolUse / PostToolUseFailure hook entry (Phase 2.8 M2).

Invoked by cc via `.claude/settings.local.json` hooks block on every
tool call (matcher `*` covers native + MCP tools). cc pipes the hook
payload JSON to stdin and discards stdout; the hook must:

- Always exit 0 (any non-zero may crash cc's main loop)
- Be cheap on the no-recording path (the common case)
- Bootstrap its own import path — cc spawns us with arbitrary cwd

We sit `packages/one-context/` on sys.path via `__file__`, then call
`one_context.recorder.hook_writer.process_hook` which handles all
filtering, no-op-when-not-recording, and append-to-rounds.jsonl logic.

NOTE: the file uses only stdlib so any system python3 works (cc users
are not guaranteed to have a venv on PATH).
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_sys_path() -> None:
    here = Path(__file__).resolve()
    # scripts/onecxt-recorder-hook.py → packages/one-context/
    pkg_root = here.parent.parent
    candidate = str(pkg_root)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


def main() -> int:
    try:
        _bootstrap_sys_path()
        try:
            from one_context.recorder.hook_writer import process_hook
        except Exception:
            # Import failure (e.g. truly broken installation) — be silent.
            return 0
        try:
            stdin_text = sys.stdin.read()
        except Exception:
            return 0
        try:
            process_hook(stdin_text)
        except Exception:
            pass
    except Exception:
        # Last-resort guard: hook never crashes cc.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
