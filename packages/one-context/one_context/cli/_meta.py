"""Meta-information commands: doctor, sync, repo, workspace, context, profile, agent.

Each `_cmd_*` returns an int exit code. `register(sub)` wires the
sub-parsers onto the top-level parser built by `cli/__init__.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from one_context.agents import load_agents
from one_context.context import build_workspace_context, render_workspace_context
from one_context.dotenv import load_dotenv
from one_context.profiles import load_mixins, load_profiles, resolve_profile
from one_context.repos import load_repos
from one_context.sync import sync_repositories
from one_context.validate import doctor, generate_knowledge_graph, workspace_context_summary
from one_context.workspaces import load_workspaces


# ── doctor / sync ────────────────────────────────────────────────────────

def _cmd_doctor(root: Path, args: argparse.Namespace) -> int:
    load_dotenv(root / ".env")

    if getattr(args, "graph", False):
        print(generate_knowledge_graph(root))
        return 0

    result = doctor(root)
    for msg in result.errors:
        print(f"error: {msg}", file=sys.stderr)
    for msg in result.warnings:
        print(f"warning: {msg}")
    for msg in result.info:
        print(f"info: {msg}")
    return 1 if result.errors else 0


def _cmd_sync(root: Path, args: argparse.Namespace) -> int:
    load_dotenv(root / ".env")
    select = list(args.select) if args.select else None
    results = sync_repositories(root, select, workers=args.jobs)
    return 1 if any(not result.success for result in results) else 0


# ── repo ─────────────────────────────────────────────────────────────────

def _cmd_repo_list(root: Path, _args: argparse.Namespace) -> int:
    entries, _ = load_repos(root)
    for e in entries:
        desc = e.get("description") or ""
        line = f"{e['id']}\t{e['url']}\t{e['path']}"
        if desc:
            line += f"\t# {desc}"
        print(line)
    return 0


# ── workspace / context ──────────────────────────────────────────────────

def _cmd_manifest_list(
    loader, label: str, root: Path, _args: argparse.Namespace,
) -> int:
    """Generic list handler for workspaces / profiles."""
    entries, _ = loader(root)
    if not entries:
        print(f"(no {label}.yaml or empty {label} list)")
        return 0
    for entry in entries:
        eid = entry.get("id", "")
        name = entry.get("name", "")
        print(f"{eid}\t{name}")
    return 0


def _cmd_workspace_list(root: Path, args: argparse.Namespace) -> int:
    return _cmd_manifest_list(load_workspaces, "workspaces", root, args)


def _cmd_workspace_show(root: Path, args: argparse.Namespace) -> int:
    try:
        data = workspace_context_summary(root, args.id)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(render_workspace_context(data, "json"), end="")
    return 0


def _cmd_context_export(root: Path, args: argparse.Namespace) -> int:
    try:
        data = build_workspace_context(root, args.id)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    rendered = render_workspace_context(data, args.format)
    if getattr(args, "compress", False) or getattr(args, "target_tokens", None) is not None:
        from one_context.context import apply_context_compression

        rendered = apply_context_compression(
            rendered,
            compress=bool(getattr(args, "compress", False)),
            target_tokens=getattr(args, "target_tokens", None),
        )
    if args.output is None:
        print(rendered, end="")
        return 0

    target = args.output.expanduser()
    if not target.is_absolute():
        target = root / target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8")
    return 0


# ── profile ──────────────────────────────────────────────────────────────

def _cmd_profile_list(root: Path, _args: argparse.Namespace) -> int:
    profiles, _ = load_profiles(root)
    mixins, _ = load_mixins(root)
    if not profiles and not mixins:
        print("(no profiles.yaml or empty)")
        return 0
    for p in profiles:
        pid = p.get("id", "")
        name = p.get("name", "")
        extends = p.get("extends", "")
        tag = f"[extends {extends}]" if extends else ""
        mxs = p.get("mixins")
        if mxs:
            tag += f" [mixins: {', '.join(mxs)}]"
        print(f"{pid}\t{name}\tprofile\t{tag}".rstrip())
    for m in mixins:
        mid = m.get("id", "")
        name = m.get("name", "")
        print(f"{mid}\t{name}\tmixin")
    return 0


def _cmd_profile_show(root: Path, args: argparse.Namespace) -> int:
    profiles, profiles_by_id = load_profiles(root)
    mixins, mixins_by_id = load_mixins(root)

    lk = args.id.casefold()
    entry = profiles_by_id.get(lk) or mixins_by_id.get(lk)
    if entry is None:
        print(f"error: unknown profile or mixin id {args.id!r}", file=sys.stderr)
        return 2

    if args.resolved and lk in profiles_by_id:
        resolved = resolve_profile(args.id, profiles_by_id, mixins_by_id)
        print(json.dumps(resolved, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


# ── agent ────────────────────────────────────────────────────────────────

def _cmd_agent_list(root: Path, _args: argparse.Namespace) -> int:
    agents, _ = load_agents(root)
    if not agents:
        print("(no agents.yaml or empty agents list)")
        return 0
    for a in agents:
        aid = a.get("id", "")
        name = a.get("name", "")
        role = a.get("role", "")
        print(f"{aid}\t{name}\t{role}")
    return 0


def _cmd_agent_show(root: Path, args: argparse.Namespace) -> int:
    agents, agents_by_id = load_agents(root)

    lk = args.id.casefold()
    entry = agents_by_id.get(lk)
    if entry is None:
        print(f"error: unknown agent id {args.id!r}", file=sys.stderr)
        return 2

    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


# ── subparser registration ──────────────────────────────────────────────

def register(sub: argparse._SubParsersAction) -> None:
    p_doctor = sub.add_parser("doctor", help="Validate manifests and local clone state")
    p_doctor.add_argument(
        "--graph",
        action="store_true",
        default=False,
        help="Output a Mermaid knowledge graph instead of running checks",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_sync = sub.add_parser(
        "sync",
        help="Clone or fast-forward pull repositories from meta/repos.yaml",
    )
    p_sync.add_argument(
        "select",
        nargs="*",
        metavar="ID_OR_ALIAS",
        help="If set, only these repo ids or aliases (case-insensitive)",
    )
    p_sync.add_argument(
        "--jobs", "-j",
        type=int,
        default=4,
        metavar="N",
        help="Number of parallel workers (default: 4, use 1 for serial)",
    )
    p_sync.set_defaults(func=_cmd_sync)

    p_repo = sub.add_parser("repo", help="Repository commands")
    repo_sub = p_repo.add_subparsers(dest="repo_command", required=True)
    p_repo_list = repo_sub.add_parser("list", help="List registered repositories")
    p_repo_list.set_defaults(func=_cmd_repo_list)

    p_ws = sub.add_parser("workspace", help="Workspace commands")
    ws_sub = p_ws.add_subparsers(dest="ws_command", required=True)
    p_ws_list = ws_sub.add_parser("list", help="List workspaces from meta/workspaces.yaml")
    p_ws_list.set_defaults(func=_cmd_workspace_list)
    p_ws_show = ws_sub.add_parser(
        "show",
        help="Print workspace definition and resolved repo paths (JSON)",
    )
    p_ws_show.add_argument("id", metavar="WORKSPACE_ID", help="Workspace id")
    p_ws_show.set_defaults(func=_cmd_workspace_show)

    p_ctx = sub.add_parser("context", help="Context export commands")
    ctx_sub = p_ctx.add_subparsers(dest="context_command", required=True)
    p_ctx_export = ctx_sub.add_parser(
        "export",
        help="Export a minimal workspace context bundle",
    )
    p_ctx_export.add_argument("id", metavar="WORKSPACE_ID", help="Workspace id")
    p_ctx_export.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format (default: json)",
    )
    p_ctx_export.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write export to a file instead of stdout",
    )
    p_ctx_export.add_argument(
        "--compress",
        action="store_true",
        default=False,
        help="Apply automatic token-budget compression to the export (approximate)",
    )
    p_ctx_export.add_argument(
        "--target-tokens",
        type=int,
        default=None,
        metavar="N",
        help="Approximate max tokens when compressing (implies compression if set)",
    )
    p_ctx_export.set_defaults(func=_cmd_context_export)

    p_prof = sub.add_parser("profile", help="Profile commands")
    prof_sub = p_prof.add_subparsers(dest="prof_command", required=True)
    p_prof_list = prof_sub.add_parser(
        "list", help="List profiles and mixins from meta/profiles.yaml",
    )
    p_prof_list.set_defaults(func=_cmd_profile_list)

    p_prof_show = prof_sub.add_parser(
        "show", help="Show a profile or mixin definition",
    )
    p_prof_show.add_argument("id", metavar="PROFILE_ID", help="Profile or mixin id")
    p_prof_show.add_argument(
        "--resolved",
        action="store_true",
        default=False,
        help="Output the fully-resolved profile after inheritance and mixin merge",
    )
    p_prof_show.set_defaults(func=_cmd_profile_show)

    p_agent = sub.add_parser("agent", help="Agent commands")
    agent_sub = p_agent.add_subparsers(dest="agent_command", required=True)
    p_agent_list = agent_sub.add_parser(
        "list", help="List agents from meta/agents.yaml",
    )
    p_agent_list.set_defaults(func=_cmd_agent_list)

    p_agent_show = agent_sub.add_parser(
        "show", help="Show an agent definition",
    )
    p_agent_show.add_argument("id", metavar="AGENT_ID", help="Agent id")
    p_agent_show.set_defaults(func=_cmd_agent_show)
