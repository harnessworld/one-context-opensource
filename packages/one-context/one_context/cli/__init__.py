"""`onecxt` CLI entry point and parser assembly.

Sub-modules each own a coherent slice of the CLI:
    _meta      doctor / sync / repo / workspace / context / profile / agent
    _worktree  worktree setup/status/teardown
    _skills    Claude Code runtime skills list/install/uninstall
    _adapt     adapter generation + git hook install/uninstall
    _smart_commit deterministic multi-repo commit/push workflow

(Skill 评测 CLI 由 `one_context.eval_lite` 提供，见 `feature skill-systematic-eval`。)

Each sub-module exposes a `register(sub: _SubParsersAction)` function
that registers its subparsers. This file just chains them.

For backwards compat with existing tests that import private handlers
directly (`from one_context.cli import _cmd_adapt`), every public
`_cmd_*` is re-exported below.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from one_context import __version__
from one_context.errors import ManifestError
from one_context.log import setup_logging
from one_context.root import find_root

from one_context.cli import (
    _adapt, _meta, _skills, _smart_commit, _syseval, _worktree,
)

# Re-exports for backwards compat with tests:
from one_context.cli._adapt import (  # noqa: F401
    _check_generated,
    _cmd_adapt,
    _cmd_adapt_install,
    _cmd_adapt_uninstall,
    _emit_file,
    _print_dry_run_block,
    _report_dirty_files,
)
from one_context.cli._meta import (  # noqa: F401
    _cmd_agent_list,
    _cmd_agent_show,
    _cmd_context_export,
    _cmd_doctor,
    _cmd_manifest_list,
    _cmd_profile_list,
    _cmd_profile_show,
    _cmd_repo_list,
    _cmd_sync,
    _cmd_workspace_list,
    _cmd_workspace_show,
)
from one_context.cli._skills import (  # noqa: F401
    _cmd_skills_install,
    _cmd_skills_list,
    _cmd_skills_uninstall,
)
from one_context.cli._smart_commit import _cmd_smart_commit  # noqa: F401
from one_context.cli._worktree import (  # noqa: F401
    _cmd_worktree_setup,
    _cmd_worktree_status,
    _cmd_worktree_teardown,
)

logger = logging.getLogger("one_context.cli")


def _resolve_root(path: Path | None) -> Path:
    if path is not None:
        root = path.expanduser().resolve()
        meta = root / "meta"
        if not (
            (meta / "repos.yaml").is_file() or (meta / "agents.yaml").is_file()
        ):
            raise ManifestError(
                f"Not a one-context root (missing meta/repos.yaml and "
                f"meta/agents.yaml): {root}"
            )
        return root
    return find_root()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onecxt",
        description="one-context — multi-repository workspace CLI",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="one-context root (directory containing meta/repos.yaml). "
        "Default: walk parents from cwd or use ONECXT_ROOT.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging to stderr",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    _meta.register(sub)
    _adapt.register(sub)
    _worktree.register(sub)
    _skills.register(sub)
    _smart_commit.register(sub)
    _syseval.register(sub)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    try:
        root = _resolve_root(args.root)
    except ManifestError as e:
        print(e.message, file=sys.stderr)
        raise SystemExit(1) from e

    raise SystemExit(args.func(root, args))
