"""Cross-platform ``onecxt smart-commit`` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from one_context.smart_commit import (
    SmartCommitError,
    build_plan,
    execute_plan,
    format_plan,
    format_result,
    load_config,
)


def _parse_mapping(values: list[str], option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key.strip() or not item.strip():
            raise SmartCommitError(f"{option} requires REPOSITORY=VALUE, got {value!r}")
        result[key.strip()] = item.strip()
    return result


def _cmd_smart_commit(root: Path, args: argparse.Namespace) -> int:
    try:
        config = load_config(root)
        if args.transport is not None:
            config.transport = args.transport
        if args.proxy is not None:
            config.https_proxy = args.proxy or None
        if args.timeout is not None:
            if args.timeout < 1:
                raise SmartCommitError("--timeout must be at least 1 second")
            config.timeout_seconds = args.timeout
        if args.jobs is not None:
            if args.jobs < 1:
                raise SmartCommitError("--jobs must be at least 1")
            config.jobs = args.jobs
        config.messages.update(_parse_mapping(args.message, "--message"))
        config.branches.update(_parse_mapping(args.target_branch, "--target-branch"))
        if args.co_author:
            config.coauthors = tuple(args.co_author)
        if args.ignore_github_token:
            config.inherit_github_token = False

        plan = build_plan(
            root,
            mode=args.mode,
            config=config,
            push=args.push,
            allow_delete=args.allow_delete,
            allow_rename=args.allow_rename,
            allow_sensitive=args.allow_sensitive,
        )
        print(format_plan(plan, push=args.push))
        if args.plan_id is not None and args.plan_id != plan.plan_id:
            raise SmartCommitError(
                f"reviewed plan ID {args.plan_id!r} does not match current plan "
                f"{plan.plan_id!r}; review the current plan before executing"
            )
        if not plan.active_repos:
            return 0
        if not plan.safe:
            return 2
        if not args.yes:
            print(
                f"\nPlan only: rerun with --yes --plan-id {plan.plan_id} "
                "using the same review options."
            )
            return 3

        result = execute_plan(plan, push=args.push)
        print("\n" + format_result(result))
        return 0
    except SmartCommitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "smart-commit",
        help="Safely classify, commit, and optionally push the main repo and submodules",
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=("full", "main"),
        default="full",
        help="Repository scope (default: full)",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Execute only when the generated plan passes every safety gate",
    )
    parser.add_argument(
        "--plan-id",
        default=None,
        metavar="ID",
        help="Require the current plan to match a previously reviewed Plan ID",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        default=False,
        help="Preflight authentication, push commits, and verify remote hashes",
    )
    parser.add_argument(
        "--transport",
        choices=("auto", "ssh", "https"),
        default=None,
        help="Push transport; auto tries configured transport_order",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        metavar="URL",
        help="HTTPS proxy for this run; use an empty value to disable local config",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Per-command timeout",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=None,
        metavar="N",
        help="Parallel repository workers",
    )
    parser.add_argument(
        "--message",
        action="append",
        default=[],
        metavar="REPOSITORY=MESSAGE",
        help="Override a generated commit message; repeat per repository",
    )
    parser.add_argument(
        "--target-branch",
        action="append",
        default=[],
        metavar="REPOSITORY=BRANCH",
        help="Override the push branch, especially for detached submodules",
    )
    parser.add_argument(
        "--co-author",
        action="append",
        default=[],
        metavar="NAME <EMAIL>",
        help="Append an explicit Co-Authored-By trailer; omitted by default",
    )
    parser.add_argument(
        "--ignore-github-token",
        action="store_true",
        default=False,
        help="For HTTPS, ignore GITHUB_TOKEN/GH_TOKEN and use the configured credential helper",
    )
    parser.add_argument(
        "--allow-delete",
        action="store_true",
        default=False,
        help="Allow reviewed tracked deletions",
    )
    parser.add_argument(
        "--allow-rename",
        action="store_true",
        default=False,
        help="Allow reviewed tracked renames",
    )
    parser.add_argument(
        "--allow-sensitive",
        action="store_true",
        default=False,
        help="Allow reviewed sensitive-word matches",
    )
    parser.set_defaults(func=_cmd_smart_commit)
