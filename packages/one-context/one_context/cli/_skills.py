"""Runtime skills: list / install / uninstall (Claude Code, Codex)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_skills_list(root: Path, args: argparse.Namespace) -> int:
    from one_context.skills import list_skills_install_status

    result = list_skills_install_status(root, tool=args.tool)
    installed_set = set(result["installed"])

    print(f"Project skills ({args.tool}):")
    for name in result["project"]:
        marker = "[x]" if name in installed_set else "[ ]"
        print(f"  {marker} {name}")

    if result["installed"]:
        not_installed = [n for n in result["project"] if n not in installed_set]
        if not_installed:
            print(f"\nNot installed ({len(not_installed)}):")
            print(f"  {', '.join(not_installed)}")
        installed_only = [n for n in result["installed"] if n not in result["project"]]
        if installed_only:
            print(f"\nInstalled but not in project ({len(installed_only)}):")
            print(f"  {', '.join(installed_only)}")
    else:
        installed_only = [n for n in result["installed"] if n not in result["project"]]
        if installed_only:
            print(f"\nInstalled but not in project ({len(installed_only)}):")
            print(f"  {', '.join(installed_only)}")

    return 0


def _cmd_skills_install(root: Path, args: argparse.Namespace) -> int:
    from one_context.skills import discover_skills, install_skills

    if args.all:
        discovered = discover_skills(root)
        names = [s.dir_name for s in discovered]
        if not names:
            print("(no skills/ directory or empty)")
            return 0
    else:
        names = list(args.names)
        if not names:
            print("error: specify skill names or use --all", file=sys.stderr)
            return 2

    result = install_skills(root, names, tool=args.tool, force=args.force)
    for name in result.installed:
        print(f"installed: {name}")
    for name in result.skipped:
        print(f"skipped:   {name} (already installed)")
    for msg in result.errors:
        print(f"error:     {msg}", file=sys.stderr)

    return 1 if result.errors else 0


def _cmd_skills_uninstall(_root: Path, args: argparse.Namespace) -> int:
    from one_context.skills import _TOOL_SKILLS_HOME, uninstall_skills

    if args.all:
        dest_root = _TOOL_SKILLS_HOME[args.tool]()
        if not dest_root.is_dir():
            print("(no installed skills)")
            return 0
        names = sorted(d.name for d in dest_root.iterdir() if d.is_dir())
        if not names:
            print("(no installed skills)")
            return 0
    else:
        names = list(args.names)
        if not names:
            print("error: specify skill names or use --all", file=sys.stderr)
            return 2

    result = uninstall_skills(names, tool=args.tool)
    for name in result.removed:
        print(f"removed: {name}")
    for name in result.skipped:
        print(f"skipped: {name} (not installed)")

    return 0


# Destinations shown in --help, keyed by tool name.
_TOOL_HELP = {
    "claude": "~/.claude/skills/",
    "codex": "$CODEX_HOME/skills/ (default ~/.codex/skills/)",
}


def _add_tool_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--tool", choices=list(_TOOL_HELP), default="claude",
        help="Target runtime (default: claude)",
    )


def register(sub: argparse._SubParsersAction) -> None:
    p_skills = sub.add_parser("skills", help="Manage runtime skills (Claude Code, Codex)")
    skills_sub = p_skills.add_subparsers(dest="skills_command", required=True)

    p_skills_list = skills_sub.add_parser(
        "list", help="List project skills and runtime installation status",
    )
    _add_tool_arg(p_skills_list)
    p_skills_list.set_defaults(func=_cmd_skills_list)

    p_skills_install = skills_sub.add_parser(
        "install",
        help="Install project skills to a tool runtime (~/.claude/skills/ or ~/.codex/skills/)",
    )
    _add_tool_arg(p_skills_install)
    p_skills_install.add_argument(
        "names", nargs="*", metavar="SKILL", help="Skill directory names to install",
    )
    p_skills_install.add_argument(
        "--all", action="store_true", default=False, help="Install all project skills",
    )
    p_skills_install.add_argument(
        "--force", action="store_true", default=False,
        help="Replace an installed skill bundle, removing stale resources",
    )
    p_skills_install.set_defaults(func=_cmd_skills_install)

    p_skills_uninstall = skills_sub.add_parser(
        "uninstall", help="Uninstall skills from a tool runtime",
    )
    _add_tool_arg(p_skills_uninstall)
    p_skills_uninstall.add_argument(
        "names", nargs="*", metavar="SKILL", help="Skill directory names to uninstall",
    )
    p_skills_uninstall.add_argument(
        "--all", action="store_true", default=False, help="Uninstall all installed skills",
    )
    p_skills_uninstall.set_defaults(func=_cmd_skills_uninstall)
