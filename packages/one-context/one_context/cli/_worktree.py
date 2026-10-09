"""Git worktree commands: setup / status / teardown."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from one_context.errors import ManifestError


def _cmd_worktree_setup(root: Path, args: argparse.Namespace) -> int:
    from one_context.worktree import setup_worktrees

    repo_ids = args.repos.split(",") if args.repos else None
    try:
        manifest = setup_worktrees(root, args.feature_id, repo_ids)
    except (ManifestError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for wt in manifest.get("worktrees", []):
        print(f"  {wt['repo_id']}\t{wt['path']}\t(branch: {wt['branch']})")
    print(f"worktrees.yaml written for feature {args.feature_id}")
    return 0


def _cmd_worktree_status(root: Path, args: argparse.Namespace) -> int:
    from one_context.worktree import status_worktrees

    try:
        manifest = status_worktrees(root, args.feature_id)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    for wt in manifest.get("worktrees", []):
        print(f"  {wt.get('repo_id')}\t{wt.get('status')}\t{wt.get('path')}\t{wt.get('branch')}")
    return 0


def _cmd_worktree_teardown(root: Path, args: argparse.Namespace) -> int:
    from one_context.worktree import teardown_worktrees

    final_status = args.status if args.status else "merged"
    try:
        manifest = teardown_worktrees(root, args.feature_id, final_status)
    except (ManifestError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    for wt in manifest.get("worktrees", []):
        print(f"  {wt.get('repo_id')}\t{wt.get('status')}")
    print(f"worktrees.yaml updated for feature {args.feature_id}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p_wt = sub.add_parser("worktree", help="Git worktree commands")
    wt_sub = p_wt.add_subparsers(dest="wt_command", required=True)

    p_wt_setup = wt_sub.add_parser("setup", help="Create worktrees for a feature")
    p_wt_setup.add_argument("feature_id", metavar="FEATURE_ID", help="Feature id (kebab-case)")
    p_wt_setup.add_argument(
        "--repos", default=None,
        help="Comma-separated repo ids (default: all repos)",
    )
    p_wt_setup.set_defaults(func=_cmd_worktree_setup)

    p_wt_status = wt_sub.add_parser("status", help="Show worktree status for a feature")
    p_wt_status.add_argument("feature_id", metavar="FEATURE_ID", help="Feature id")
    p_wt_status.set_defaults(func=_cmd_worktree_status)

    p_wt_teardown = wt_sub.add_parser("teardown", help="Remove worktrees for a feature")
    p_wt_teardown.add_argument("feature_id", metavar="FEATURE_ID", help="Feature id")
    p_wt_teardown.add_argument(
        "--status", choices=("merged", "abandoned"), default="merged",
        help="Status to set for removed worktrees (default: merged)",
    )
    p_wt_teardown.set_defaults(func=_cmd_worktree_teardown)
