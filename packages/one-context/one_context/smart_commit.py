"""Deterministic, cross-platform Smart Commit engine.

The engine intentionally keeps judgement and mutation separate: ``build_plan``
only inspects repositories, while ``execute_plan`` refuses to mutate unless the
plan passes every safety gate.  All subprocesses use argument arrays with
``shell=False`` (the default), so the same code path works on macOS and Windows.
"""

from __future__ import annotations

import configparser
import fnmatch
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlparse

import yaml


DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_SSH_CONNECT_TIMEOUT_SECONDS = 15
DEFAULT_JOBS = 4
DEFAULT_TRANSPORT_ORDER = ("ssh", "https")


class SmartCommitError(RuntimeError):
    """An actionable Smart Commit failure."""


@dataclass(frozen=True)
class Change:
    path: str
    status: str
    xy: str
    tracked: bool
    original_path: str | None = None


@dataclass(frozen=True)
class Classification:
    change: Change
    mark: str
    reason: str


@dataclass(frozen=True)
class SensitiveRule:
    keyword: str
    category: str = ""
    note: str = ""
    match: str = "word"
    allow_patterns: tuple[str, ...] = ()
    allow_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SensitiveHit:
    path: str
    line: int
    keyword: str
    category: str
    context: str


@dataclass(frozen=True)
class PushPlan:
    transport: str
    url: str
    branch: str
    prefix: tuple[str, ...]
    set_upstream: bool
    ignore_github_token: bool = False

    def command(self, *, dry_run: bool, source: str = "HEAD") -> list[str]:
        command = [*self.prefix, "push"]
        if dry_run:
            command.append("--dry-run")
        if self.set_upstream and source == "HEAD":
            command.append("--set-upstream")
        command.extend(("origin", f"{source}:refs/heads/{self.branch}"))
        return command


@dataclass(frozen=True)
class RepoSnapshot:
    head: str
    branch: str
    status: tuple[Change, ...]
    index: dict[str, tuple[str, str, str]]
    # raw digest, Git mode, canonical blob id (after clean/CRLF conversion).
    contents: dict[str, tuple[str, str, str] | None]


@dataclass
class RepoState:
    name: str
    path: Path
    relative_path: str
    is_main: bool
    changes: list[Change] = field(default_factory=list)
    classifications: list[Classification] = field(default_factory=list)
    sensitive_hits: list[SensitiveHit] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    branch: str = ""
    target_branch: str = ""
    remote_url: str = ""
    status_snapshot: bytes = b""
    push_plan: PushPlan | None = None
    push_attempts: list[str] = field(default_factory=list)
    commit_hash: str = ""
    commit_message: str = ""
    pushed: bool = False
    pushed_hash: str = ""
    remote_ahead: bool = False
    snapshot: RepoSnapshot | None = None
    push_required: bool = False
    remote_hash: str = ""
    push_classifications: list[Classification] = field(default_factory=list)
    push_sensitive_hits: list[SensitiveHit] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


@dataclass
class SmartCommitConfig:
    transport: str = "auto"
    transport_order: tuple[str, ...] = DEFAULT_TRANSPORT_ORDER
    https_proxy: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    ssh_connect_timeout_seconds: int = DEFAULT_SSH_CONNECT_TIMEOUT_SECONDS
    jobs: int = DEFAULT_JOBS
    branches: dict[str, str] = field(default_factory=dict)
    messages: dict[str, str] = field(default_factory=dict)
    coauthors: tuple[str, ...] = ()
    inherit_github_token: bool = True


@dataclass
class SmartCommitPlan:
    root: Path
    mode: str
    repos: list[RepoState]
    config: SmartCommitConfig
    allow_delete: bool = False
    allow_rename: bool = False
    allow_sensitive: bool = False
    push_requested: bool = False

    @property
    def plan_id(self) -> str:
        """Stable identifier for the exact reviewed repository state and options."""

        def change_value(change: Change) -> tuple[object, ...]:
            return (
                change.path,
                change.status,
                change.xy,
                change.tracked,
                change.original_path,
            )

        def classifications(items: Sequence[Classification]) -> list[tuple[object, ...]]:
            return [(*change_value(item.change), item.mark, item.reason) for item in items]

        def hits(items: Sequence[SensitiveHit]) -> list[tuple[object, ...]]:
            return [
                (item.path, item.line, item.keyword, item.category, item.context) for item in items
            ]

        repositories = []
        for repo in self.repos:
            snapshot = repo.snapshot
            snapshot_value = None
            if snapshot is not None:
                snapshot_value = {
                    "head": snapshot.head,
                    "branch": snapshot.branch,
                    "status": [change_value(item) for item in snapshot.status],
                    "index": sorted((path, *value) for path, value in snapshot.index.items()),
                    "contents": sorted(
                        (path, None if value is None else list(value))
                        for path, value in snapshot.contents.items()
                    ),
                }
            push_plan = repo.push_plan
            repositories.append(
                {
                    "name": repo.name,
                    "relative_path": repo.relative_path,
                    "is_main": repo.is_main,
                    "target_branch": repo.target_branch,
                    "remote_url": repo.remote_url,
                    "snapshot": snapshot_value,
                    "classifications": classifications(repo.classifications),
                    "sensitive_hits": hits(repo.sensitive_hits),
                    "push_required": repo.push_required,
                    "remote_hash": repo.remote_hash,
                    "push_classifications": classifications(repo.push_classifications),
                    "push_sensitive_hits": hits(repo.push_sensitive_hits),
                    "push_plan": None
                    if push_plan is None
                    else {
                        "transport": push_plan.transport,
                        "url": push_plan.url,
                        "branch": push_plan.branch,
                        "set_upstream": push_plan.set_upstream,
                        "ignore_github_token": push_plan.ignore_github_token,
                    },
                }
            )
        payload = {
            "version": 1,
            "root": str(self.root.resolve()),
            "mode": self.mode,
            "push": self.push_requested,
            "allow_delete": self.allow_delete,
            "allow_rename": self.allow_rename,
            "allow_sensitive": self.allow_sensitive,
            "config": {
                "transport": self.config.transport,
                "transport_order": self.config.transport_order,
                "https_proxy": self.config.https_proxy,
                "branches": sorted(self.config.branches.items()),
                "messages": sorted(self.config.messages.items()),
                "coauthors": self.config.coauthors,
                "inherit_github_token": self.config.inherit_github_token,
            },
            "repositories": repositories,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:20]

    @property
    def changed_repos(self) -> list[RepoState]:
        return [repo for repo in self.repos if repo.has_changes]

    @property
    def active_repos(self) -> list[RepoState]:
        return [
            repo for repo in self.repos if repo.has_changes or repo.push_required or repo.errors
        ]

    @property
    def blockers(self) -> list[str]:
        result: list[str] = []
        for repo in self.active_repos:
            result.extend(f"{repo.name}: {message}" for message in repo.errors)
            for item in [*repo.classifications, *repo.push_classifications]:
                # SKIP means "intentionally excluded from this commit" (secrets,
                # generated media, build artifacts). It is a passing state, not a
                # blocker: the engine simply does not stage these paths.
                if item.mark not in ("OK", "SKIP"):
                    result.append(f"{repo.name}: {item.mark} {item.change.path} ({item.reason})")
                if item.change.status == "U":
                    result.append(f"{repo.name}: unresolved merge conflict: {item.change.path}")
                if item.change.status == "D" and not self.allow_delete:
                    result.append(
                        f"{repo.name}: deletion requires --allow-delete: {item.change.path}"
                    )
                if item.change.status == "R" and not self.allow_rename:
                    result.append(
                        f"{repo.name}: rename requires --allow-rename: {item.change.path}"
                    )
            for item in repo.push_classifications:
                if item.mark == "SKIP":
                    result.append(
                        f"{repo.name}: excluded path is already in an unpushed commit: "
                        f"{item.change.path}; review the pending history before pushing"
                    )
            sensitive_count = len(repo.sensitive_hits) + len(repo.push_sensitive_hits)
            if sensitive_count and not self.allow_sensitive:
                result.append(
                    f"{repo.name}: {sensitive_count} sensitive match(es) require "
                    "--allow-sensitive after review"
                )
        return result

    @property
    def safe(self) -> bool:
        return not self.blockers


def _run_git(
    repo: Path,
    args: Sequence[str],
    *,
    timeout: int,
    text: bool = True,
    input_data: bytes | str | None = None,
) -> subprocess.CompletedProcess:
    try:
        kwargs = {
            "cwd": str(repo),
            "capture_output": True,
            "text": text,
            "shell": False,
            "timeout": timeout,
            "check": False,
            "input": input_data,
        }
        if text:
            kwargs.update(encoding="utf-8", errors="replace")
        return subprocess.run(["git", *args], **kwargs)
    except FileNotFoundError as exc:
        raise SmartCommitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SmartCommitError(
            f"git command timed out after {timeout}s in {repo}: git {' '.join(args)}"
        ) from exc


def _git_text(repo: Path, args: Sequence[str], *, timeout: int) -> str:
    result = _run_git(repo, args, timeout=timeout)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "git command failed").strip()
        raise SmartCommitError(message)
    return result.stdout.strip()


def load_config(root: Path) -> SmartCommitConfig:
    """Load ignored, per-machine settings from ``.onecxt/local.yaml``."""
    path = root / ".onecxt" / "local.yaml"
    raw: dict = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise SmartCommitError(f"{path} must contain a YAML mapping")
        raw = loaded.get("smart_commit") or {}
        if not isinstance(raw, dict):
            raise SmartCommitError(f"smart_commit in {path} must be a mapping")

    transport = str(raw.get("transport", "auto")).lower()
    raw_order = raw.get("transport_order", DEFAULT_TRANSPORT_ORDER)
    if not isinstance(raw_order, (list, tuple)):
        raise SmartCommitError("smart_commit.transport_order must be a list")
    order = tuple(str(item).lower() for item in raw_order)
    if transport not in {"auto", "ssh", "https"}:
        raise SmartCommitError("smart_commit.transport must be auto, ssh, or https")
    if not order or any(item not in {"ssh", "https"} for item in order):
        raise SmartCommitError("smart_commit.transport_order may only contain ssh and https")

    env_proxy = os.environ.get("ONECXT_HTTPS_PROXY", "").strip()
    proxy_value = env_proxy or str(raw.get("https_proxy") or "").strip()
    branches = raw.get("branches") or {}
    messages = raw.get("messages") or {}
    coauthors = raw.get("coauthors") or []
    if not isinstance(branches, dict) or not isinstance(messages, dict):
        raise SmartCommitError("smart_commit.branches and messages must be mappings")
    if not isinstance(coauthors, list):
        raise SmartCommitError("smart_commit.coauthors must be a list")

    return SmartCommitConfig(
        transport=transport,
        transport_order=order,
        https_proxy=proxy_value or None,
        timeout_seconds=max(1, int(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))),
        ssh_connect_timeout_seconds=max(
            1,
            int(raw.get("ssh_connect_timeout_seconds", DEFAULT_SSH_CONNECT_TIMEOUT_SECONDS)),
        ),
        jobs=max(1, int(raw.get("jobs", DEFAULT_JOBS))),
        branches={str(key): str(value) for key, value in branches.items()},
        messages={str(key): str(value) for key, value in messages.items()},
        coauthors=tuple(str(item) for item in coauthors),
        inherit_github_token=bool(raw.get("inherit_github_token", True)),
    )


def discover_repositories(root: Path, mode: str) -> list[RepoState]:
    repos = [RepoState("main", root, ".", True)]
    if mode == "main":
        return repos

    modules = root / ".gitmodules"
    if not modules.is_file():
        return repos

    parser = configparser.RawConfigParser()
    parser.read(modules, encoding="utf-8")
    for section in parser.sections():
        if not section.startswith("submodule ") or not parser.has_option(section, "path"):
            continue
        relative = parser.get(section, "path").strip().replace("\\", "/")
        name_match = re.match(r'^submodule\s+"(.*)"$', section)
        name = name_match.group(1) if name_match else relative
        repos.append(RepoState(name, root / Path(relative), relative, False))
    return repos


def parse_porcelain_v2(data: bytes) -> list[Change]:
    """Parse ``git status --porcelain=v2 -z`` without losing path characters."""
    records = data.split(b"\0")
    changes: list[Change] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        kind = record[:1]
        if kind == b"?":
            changes.append(Change(os.fsdecode(record[2:]), "?", "??", False))
            continue
        if kind == b"!":
            continue
        if kind == b"1":
            parts = record.split(b" ", 8)
            if len(parts) != 9:
                raise SmartCommitError(f"unexpected porcelain v2 record: {record!r}")
            xy = parts[1].decode("ascii", "replace")
            changes.append(Change(os.fsdecode(parts[8]), _primary_status(xy), xy, True))
            continue
        if kind == b"2":
            parts = record.split(b" ", 9)
            if len(parts) != 10 or index >= len(records):
                raise SmartCommitError(f"unexpected rename record: {record!r}")
            original = os.fsdecode(records[index])
            index += 1
            xy = parts[1].decode("ascii", "replace")
            changes.append(Change(os.fsdecode(parts[9]), "R", xy, True, original))
            continue
        if kind == b"u":
            parts = record.split(b" ", 10)
            path = os.fsdecode(parts[-1])
            changes.append(Change(path, "U", "UU", True))
            continue
        raise SmartCommitError(f"unsupported porcelain v2 record: {record!r}")
    return changes


def _primary_status(xy: str) -> str:
    for status in ("U", "R", "D", "A", "M", "T"):
        if status in xy:
            return status
    return xy.strip(".") or "M"


def _scan_repo(repo: RepoState, config: SmartCommitConfig) -> RepoState:
    if not repo.path.is_dir():
        repo.errors.append(
            f"submodule path is missing: {repo.path}; run git submodule update --init --recursive"
        )
        return repo

    status = _run_git(
        repo.path,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        timeout=config.timeout_seconds,
        text=False,
    )
    if status.returncode != 0:
        stderr = os.fsdecode(status.stderr or b"").strip()
        repo.errors.append(stderr or "git status failed")
        return repo
    repo.status_snapshot = status.stdout
    repo.changes = parse_porcelain_v2(status.stdout)

    try:
        repo.branch = _git_text(
            repo.path,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
            timeout=config.timeout_seconds,
        )
    except SmartCommitError:
        repo.branch = ""
    configured_branch = config.branches.get(repo.name) or config.branches.get(repo.relative_path)
    repo.target_branch = configured_branch or repo.branch or "main"

    if repo.has_changes:
        for key in ("user.name", "user.email"):
            result = _run_git(repo.path, ["config", "--get", key], timeout=config.timeout_seconds)
            if result.returncode != 0 or not result.stdout.strip():
                repo.errors.append(f"git {key} is not configured")
    remote = _run_git(
        repo.path,
        ["remote", "get-url", "--push", "origin"],
        timeout=config.timeout_seconds,
    )
    if remote.returncode == 0:
        repo.remote_url = remote.stdout.strip()
    _check_git_state(repo, config)
    return repo


def _check_git_state(repo: RepoState, config: SmartCommitConfig) -> None:
    for state_name in ("index.lock", "MERGE_HEAD", "rebase-apply", "rebase-merge"):
        result = _run_git(
            repo.path,
            ["rev-parse", "--git-path", state_name],
            timeout=config.timeout_seconds,
        )
        if result.returncode != 0:
            continue
        candidate = Path(result.stdout.strip())
        if not candidate.is_absolute():
            candidate = repo.path / candidate
        if candidate.exists():
            repo.errors.append(f"Git operation in progress or lock present: {state_name}")


def scan_repositories(repos: list[RepoState], config: SmartCommitConfig) -> list[RepoState]:
    if shutil.which("git") is None:
        raise SmartCommitError("git is not installed or not on PATH")
    workers = min(config.jobs, max(1, len(repos)))
    by_index: dict[int, RepoState] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_repo, repo, config): index for index, repo in enumerate(repos)}
        for future in as_completed(futures):
            by_index[futures[future]] = future.result()
    return [by_index[index] for index in range(len(repos))]


def classify_change(repo: RepoState, change: Change, submodule_paths: set[str]) -> Classification:
    path = change.path.replace("\\", "/")
    parts = tuple(part for part in path.split("/") if part)
    lowered = tuple(part.casefold() for part in parts)

    if repo.is_main and path in submodule_paths:
        return Classification(change, "OK", "submodule pointer")
    if _is_hard_excluded_path(lowered) or (
        _is_excluded_path(path, lowered) and not _is_canonical_asset(repo, path, parts)
    ):
        return Classification(change, "SKIP", "local, generated, secret, or build artifact")
    if change.tracked:
        return Classification(change, "OK", "already tracked")

    if repo.is_main:
        if path.startswith("packages/one-context/"):
            return Classification(change, "OK", "one-context CLI source or test")
        if (
            path.startswith("docs/")
            or path.startswith("meta/")
            and path.endswith((".yaml", ".yml"))
        ):
            return Classification(change, "OK", "project documentation or registry")
        if path in {".gitignore", ".gitmodules", "AGENTS.md", "README.md", "LICENSE"}:
            return Classification(change, "OK", "project-level source")
        if path.startswith(".github/"):
            return Classification(change, "OK", "project CI or workflow configuration")
        if len(parts) == 1 and path.endswith(("-lock.json", "-lock.yaml", ".lock")):
            return Classification(change, "OK", "project dependency lockfile")
        if len(parts) == 1 and (
            path.startswith("prompt-") or path.endswith((".png", ".jpg", ".md"))
        ):
            return Classification(
                change, "MOVE", "root artifact needs an explicit canonical location"
            )
        return Classification(change, "ASK", "unrecognized new main-repository path")

    repo_key = repo.relative_path.casefold()
    if repo_key == "skills" or repo.name.casefold() == "skills":
        _skill_resources = {
            "agents",
            "references",
            "assets",
            "lib",
            "templates",
            "scripts",
            "src",
            "studio",
        }
        # Top-level skill: <name>/{SKILL.md, references/..., assets/..., ...}
        if len(parts) >= 2 and (parts[1] == "SKILL.md" or parts[1] in _skill_resources):
            return Classification(change, "OK", "skill source or bundled resource")
        if parts[0] == "tools":
            inner = parts[1:]
            # Two-level tool skill: tools/<name>/{SKILL.md, references/..., ...}
            if len(inner) >= 2 and (inner[1] == "SKILL.md" or inner[1] in _skill_resources):
                return Classification(change, "OK", "second-level tool skill source")
            # Three-level tool skill: tools/<category>/<name>/{SKILL.md, references/..., ...}
            if len(inner) >= 3 and (inner[2] == "SKILL.md" or inner[2] in _skill_resources):
                return Classification(change, "OK", "third-level tool skill source")
            # Tool group index or metadata at tools/**/{README.md, _meta.json}
            if parts[-1] in {"README.md", "_meta.json"}:
                return Classification(change, "OK", "tool skill group index")
        return Classification(change, "ASK", "unrecognized new skill path")
    if repo_key == "knowledge" or repo.name.casefold() == "knowledge":
        return Classification(change, "OK", "knowledge repository content")
    if repo_key == "features" or repo.name.casefold() == "features":
        if path == "INDEX.md" or _is_feature_source(path, parts):
            return Classification(change, "OK", "feature document or Remotion source")
        return Classification(change, "ASK", "unrecognized new feature path")
    return Classification(change, "ASK", "unrecognized submodule policy")


def _is_hard_excluded_path(lowered: tuple[str, ...]) -> bool:
    name = lowered[-1] if lowered else ""
    if name == ".env" or name.endswith((".pyc", ".swp")):
        return True
    if any(
        part in {"tmp", "node_modules", "__pycache__", ".venv", ".idea", ".vscode"}
        for part in lowered
    ):
        return True
    if any(part in {".agents", ".codex", ".cursor", ".hermes"} for part in lowered):
        return True
    return False


def _is_excluded_path(path: str, lowered: tuple[str, ...]) -> bool:
    if _is_hard_excluded_path(lowered):
        return True
    name = lowered[-1] if lowered else ""
    if name.endswith((".wav", ".mp3", ".mp4", ".webm", ".png", ".jpg", ".jpeg")):
        return True
    if (
        "/production/media/" in f"/{path.casefold()}/"
        or "/production/remotion/" in f"/{path.casefold()}/"
    ):
        return True
    return False


def _is_canonical_asset(repo: RepoState, path: str, parts: tuple[str, ...]) -> bool:
    normalized = f"/{path.casefold()}/"
    if repo.is_main:
        return normalized.startswith("/docs/images/")
    repo_key = repo.relative_path.casefold()
    if repo_key == "knowledge" or repo.name.casefold() == "knowledge":
        return True
    if repo_key == "skills" or repo.name.casefold() == "skills":
        return "assets" in {part.casefold() for part in parts}
    return False


def _is_feature_source(path: str, parts: tuple[str, ...]) -> bool:
    if len(parts) < 2:
        return False
    if "/archive/" in f"/{path.casefold()}/":
        return True
    if parts[-1] in {
        "spec.md",
        "tech_design.md",
        "issue_checklist.md",
        "test_report.md",
        "mr_report.md",
        "deliver.md",
        "review_record.md",
    }:
        return True
    lowered = path.casefold()
    if f"/{lowered}".endswith("/production/cover-prompt.md"):
        return True
    if (
        "/production/content/" in f"/{lowered}"
        or "/production/timing/" in f"/{lowered}"
        or "/production/learning/" in f"/{lowered}"
        or "/production/slides/" in f"/{lowered}"
        or "/production/recorder/" in f"/{lowered}"
        or "/production/image-pages/" in f"/{lowered}"
        or "/production/cover/" in f"/{lowered}"
        or "/production/covers/" in f"/{lowered}"
        or "/production/deck/" in f"/{lowered}"
        or "/production/visual-options/" in f"/{lowered}"
    ):
        return True
    # Per-episode decomposition: teachback features may host deliverable
    # episodes under ``production/episodes/<id>/`` that reuse the same
    # skeleton buckets (content / timing / learning / slides / recorder) one
    # level deeper. Generated media under ``episodes/<id>/{stills,videos,media}/``
    # stays excluded by ``_is_excluded_path``; stray files directly under an
    # episode that are not inside a bucket stay ``ASK``.
    if "/production/episodes/" in f"/{lowered}":
        tail = f"/{lowered}".split("/production/episodes/", 1)[1]
        if (
            "/content/" in tail
            or "/timing/" in tail
            or "/learning/" in tail
            or "/slides/" in tail
            or "/recorder/" in tail
            or "/image-pages/" in tail
            or "/cover/" in tail
            or "/deck/" in tail
            or "/visual-options/" in tail
        ):
            return True
    if any(
        segment in f"/{lowered}"
        for segment in (
            "/remotion/src/",
            "/remotion/scripts/",
            "/remotion/lib/",
            "/remotion/public/fonts/",
        )
    ):
        return True
    return parts[-1] in {"package.json", "remotion.config.ts", "tsconfig.json", ".gitkeep"}


def load_sensitive_rules(root: Path) -> list[SensitiveRule]:
    path = root / ".onecxt" / "sensitive-words.yaml"
    if not path.is_file():
        return []
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = loaded.get("words") or []
    rules: list[SensitiveRule] = []
    for entry in entries:
        if not isinstance(entry, dict) or not str(entry.get("keyword") or "").strip():
            continue
        keyword = str(entry["keyword"]).strip()
        default_match = "word" if re.fullmatch(r"[A-Za-z0-9_]+", keyword) else "substring"
        match = str(entry.get("match") or default_match).lower()
        if match not in {"word", "substring", "regex"}:
            raise SmartCommitError(f"invalid sensitive match mode for {keyword!r}: {match}")
        allow_patterns = tuple(str(item) for item in entry.get("allow_patterns") or [])
        try:
            if match == "regex":
                re.compile(keyword, re.IGNORECASE)
            for pattern in allow_patterns:
                re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise SmartCommitError(f"invalid sensitive regex for {keyword!r}: {exc}") from exc
        rules.append(
            SensitiveRule(
                keyword=keyword,
                category=str(entry.get("category") or ""),
                note=str(entry.get("note") or ""),
                match=match,
                allow_patterns=allow_patterns,
                allow_paths=tuple(str(item) for item in entry.get("allow_paths") or []),
            )
        )
    return rules


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _changed_lines(repo: RepoState, change: Change, timeout: int) -> Iterable[tuple[int, str]]:
    if not change.tracked:
        path = repo.path / Path(change.path)
        try:
            payload = path.read_bytes()
        except OSError:
            return []
        if b"\0" in payload:
            return []
        return list(enumerate(payload.decode("utf-8", "replace").splitlines(), start=1))

    result = _run_git(
        repo.path,
        ["diff", "--no-ext-diff", "--no-color", "--unified=0", "HEAD", "--", change.path],
        timeout=timeout,
    )
    if result.returncode != 0:
        return []
    return _added_lines(result.stdout)


def _added_lines(diff: str) -> list[tuple[int, str]]:
    output: list[tuple[int, str]] = []
    line_number: int | None = None
    for line in diff.splitlines():
        hunk = _HUNK_RE.match(line)
        if hunk:
            line_number = int(hunk.group(1))
            continue
        if line_number is None or line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            output.append((line_number, line[1:]))
            line_number += 1
        elif line.startswith("-"):
            continue
        elif line.startswith(" "):
            line_number += 1
    return output


def _rule_matches(rule: SensitiveRule, path: str, line: str) -> bool:
    if any(fnmatch.fnmatch(path, pattern) for pattern in rule.allow_paths):
        return False
    if any(re.search(pattern, line, re.IGNORECASE) for pattern in rule.allow_patterns):
        return False
    if rule.match == "regex":
        return re.search(rule.keyword, line, re.IGNORECASE) is not None
    if rule.match == "substring":
        return rule.keyword.casefold() in line.casefold()
    pattern = rf"(?<![0-9A-Za-z_]){re.escape(rule.keyword)}(?![0-9A-Za-z_])"
    return re.search(pattern, line, re.IGNORECASE) is not None


def scan_sensitive(
    repo: RepoState, rules: Sequence[SensitiveRule], timeout: int
) -> list[SensitiveHit]:
    hits: list[SensitiveHit] = []
    if not rules:
        return hits
    for change in repo.changes:
        if change.status == "D":
            continue
        for line_number, line in _changed_lines(repo, change, timeout):
            for rule in rules:
                if _rule_matches(rule, change.path, line):
                    hits.append(
                        SensitiveHit(
                            path=change.path,
                            line=line_number,
                            keyword=rule.keyword,
                            category=rule.category,
                            context=line.strip()[:160],
                        )
                    )
    return hits


def _is_ssh_url(url: str) -> bool:
    return url.startswith("git@") or url.startswith("ssh://")


def _to_https(url: str) -> str | None:
    if url.startswith(("https://", "http://")):
        return re.sub(r"^http://", "https://", url)
    match = re.match(r"^[^@]+@([^:]+):(.+)$", url)
    if match:
        return f"https://{match.group(1)}/{match.group(2)}"
    match = re.match(r"^ssh://(?:[^@]+@)?([^/]+)/(.+)$", url)
    if match:
        return f"https://{match.group(1)}/{match.group(2)}"
    return None


def _to_ssh(url: str) -> str | None:
    if _is_ssh_url(url):
        return url
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return f"git@{parsed.hostname}:{parsed.path.lstrip('/')}"
    return None


def proxy_reachable(proxy: str, timeout: float = 2.0) -> tuple[bool, str]:
    parsed = urlparse(proxy)
    if not parsed.hostname or not parsed.port:
        return False, "proxy must include scheme, host, and port"
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout):
            return True, ""
    except OSError as exc:
        return False, str(exc)


def _has_upstream(repo: RepoState, config: SmartCommitConfig) -> bool:
    if not repo.branch:
        return False
    result = _run_git(
        repo.path,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        timeout=config.timeout_seconds,
    )
    return result.returncode == 0


def _push_prefix(
    repo: RepoState,
    config: SmartCommitConfig,
    transport: str,
    url: str,
) -> tuple[str, ...]:
    # Rewrite the configured origin URL instead of adding another
    # remote.origin.url value. Git treats that key as multi-valued, so adding
    # an override can leave the blocked original transport active.
    prefix: list[str] = ["git"]
    if url != repo.remote_url:
        prefix.extend(("-c", f"url.{url}.insteadOf={repo.remote_url}"))
    if transport == "ssh":
        ssh_command = (
            "ssh -o ProxyCommand=none -o BatchMode=yes "
            f"-o ConnectTimeout={config.ssh_connect_timeout_seconds}"
        )
        prefix.extend(("-c", f"core.sshCommand={ssh_command}"))
    elif config.https_proxy:
        prefix.extend(("-c", f"http.proxy={config.https_proxy}"))
    return tuple(prefix)


def _detect_remote_ahead(repo: RepoState, config: SmartCommitConfig) -> None:
    """Best-effort check whether the remote branch is ahead of local HEAD.

    Run only after ``select_push_plan`` has chosen a working transport, and
    reuse that plan's converted URL plus proxy/credential overrides so the
    fetch does not hang on a blocked transport (e.g. SSH when port 22 is
    firewalled).  The fetch uses a raise-safe runner: on timeout or network
    failure it is swallowed, because the push dry-run that already ran is the
    authority on whether the push can proceed.  When the remote is ahead,
    ``repo.remote_ahead`` is set to ``True`` and a descriptive error is
    appended so the plan reports the blocker.
    """
    plan = repo.push_plan
    if plan is None or not repo.remote_url or not repo.target_branch:
        return
    fetch = _run_prefixed(
        repo.path,
        [*plan.prefix, "fetch", "--quiet", "origin", repo.target_branch],
        config.timeout_seconds,
        ignore_github_token=plan.ignore_github_token,
    )
    if fetch.returncode != 0:
        return  # network/transport issue; push dry-run will report it
    local = _git_text(
        repo.path,
        ["rev-parse", "HEAD"],
        timeout=config.timeout_seconds,
    )
    remote_ref = f"refs/remotes/origin/{repo.target_branch}"
    # If the remote-tracking ref doesn't exist yet, there's nothing to compare
    rev_result = _run_git(
        repo.path,
        ["rev-parse", remote_ref],
        timeout=config.timeout_seconds,
    )
    if rev_result.returncode != 0:
        return
    remote = rev_result.stdout.strip()
    if not local or not remote or local == remote:
        return
    # Check if remote contains local (i.e. local is behind)
    merge_base = _git_text(
        repo.path,
        ["merge-base", local, remote],
        timeout=config.timeout_seconds,
    )
    if merge_base == local:
        repo.remote_ahead = True
        repo.errors.append(
            f"remote 'origin/{repo.target_branch}' is ahead of local HEAD; "
            f"run 'git fetch && git rebase origin/{repo.target_branch}' then rerun smart-commit"
        )


def select_push_plan(repo: RepoState, config: SmartCommitConfig) -> PushPlan | None:
    if not repo.remote_url:
        repo.errors.append("origin push URL is not configured")
        return None
    transports = config.transport_order if config.transport == "auto" else (config.transport,)
    for transport in transports:
        url = _to_ssh(repo.remote_url) if transport == "ssh" else _to_https(repo.remote_url)
        if not url:
            repo.push_attempts.append(f"{transport}: cannot convert remote URL")
            continue
        if transport == "https" and config.https_proxy:
            reachable, reason = proxy_reachable(config.https_proxy)
            if not reachable:
                repo.push_attempts.append(f"https: proxy unreachable ({reason})")
                continue
        plan = PushPlan(
            transport=transport,
            url=url,
            branch=repo.target_branch,
            prefix=_push_prefix(repo, config, transport, url),
            set_upstream=bool(repo.branch) and not _has_upstream(repo, config),
            ignore_github_token=transport == "https" and not config.inherit_github_token,
        )
        result = _run_prefixed(
            repo.path,
            plan.command(dry_run=True),
            config.timeout_seconds,
            ignore_github_token=plan.ignore_github_token,
        )
        if result.returncode == 0:
            return plan
        stderr_lower = (result.stderr or result.stdout or "").lower()
        message = (result.stderr or result.stdout or "push dry-run failed").strip().splitlines()
        tail = message[-1] if message else "failed"
        if any(sig in stderr_lower for sig in ("non-fast-forward", "fetch first", "rejected")):
            repo.push_attempts.append(f"{transport}: remote-ahead ({tail})")
        elif any(
            sig in stderr_lower
            for sig in (
                "permission denied",
                "authentication",
                "could not read username",
                "credentials",
                "403",
                "401",
            )
        ):
            repo.push_attempts.append(f"{transport}: auth/credentials ({tail})")
        else:
            repo.push_attempts.append(f"{transport}: {tail}")
    repo.errors.append("push preflight failed: " + "; ".join(repo.push_attempts))
    return None


def _run_prefixed(
    repo: Path,
    command: Sequence[str],
    timeout: int,
    *,
    ignore_github_token: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = None
    if ignore_github_token:
        env = os.environ.copy()
        env.pop("GITHUB_TOKEN", None)
        env.pop("GH_TOKEN", None)
    try:
        return subprocess.run(
            list(command),
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=env,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(command), 1, "", str(exc))


def _read_index(repo: RepoState, config: SmartCommitConfig) -> dict[str, tuple[str, str, str]]:
    result = _run_git(
        repo.path,
        ["ls-files", "--stage", "-z"],
        timeout=config.timeout_seconds,
        text=False,
    )
    if result.returncode:
        raise SmartCommitError(f"{repo.name}: unable to inspect the index")
    entries = {}
    for record in result.stdout.split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, oid, stage = metadata.decode("ascii").split()
            entries[os.fsdecode(path)] = (mode, oid, stage)
    return entries


def _head_identity(repo: RepoState, config: SmartCommitConfig) -> tuple[str, str]:
    head = _run_git(repo.path, ["rev-parse", "--verify", "HEAD"], timeout=config.timeout_seconds)
    branch = _run_git(
        repo.path,
        ["symbolic-ref", "--quiet", "HEAD"],
        timeout=config.timeout_seconds,
    )
    return (head.stdout.strip() if not head.returncode else "", branch.stdout.strip())


def _keeps_staged_deletion(repo: RepoState, change: Change, config: SmartCommitConfig) -> bool:
    if change.status != "D" or change.xy[:1] != "D":
        return False
    path = repo.path / change.path
    if not path.exists() and not path.is_symlink():
        return True
    ignored = _run_git(
        repo.path,
        ["check-ignore", "--quiet", "--", change.path],
        timeout=config.timeout_seconds,
    )
    return ignored.returncode == 0


def _capture_snapshot(repo: RepoState, config: SmartCommitConfig) -> RepoSnapshot:
    head, branch = _head_identity(repo, config)
    index = _read_index(repo, config)
    status = _run_git(
        repo.path,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        timeout=config.timeout_seconds,
        text=False,
    )
    if status.returncode:
        raise SmartCommitError(f"{repo.name}: unable to inspect worktree")
    contents = {}
    filemode = (
        _run_git(
            repo.path,
            ["config", "--bool", "core.filemode"],
            timeout=config.timeout_seconds,
        ).stdout.strip()
        != "false"
    )
    for item in repo.classifications:
        if item.mark != "OK":
            continue
        relative = item.change.path
        path = repo.path / relative
        if not path.exists() and not path.is_symlink():
            contents[relative] = None
            continue
        if path.is_dir() and not path.is_symlink():
            oid = _git_text(path, ["rev-parse", "HEAD"], timeout=config.timeout_seconds)
            contents[relative] = (oid, "160000", oid)
            continue
        try:
            payload = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        except OSError as exc:
            raise SmartCommitError(f"{repo.name}: cannot inspect {relative}: {exc}") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if _keeps_staged_deletion(repo, item.change, config):
            contents[relative] = (digest, "", "")
            continue
        if path.is_symlink():
            blob = _run_git(
                repo.path,
                ["hash-object", "--stdin"],
                timeout=config.timeout_seconds,
                text=False,
                input_data=payload,
            )
            mode = "120000"
        else:
            blob = _run_git(
                repo.path,
                ["hash-object", f"--path={relative}", "--", relative],
                timeout=config.timeout_seconds,
                text=False,
            )
            mode = "100755" if filemode and path.stat().st_mode & 0o111 else "100644"
            if not filemode and relative in index:
                mode = index[relative][0]
        if blob.returncode:
            raise SmartCommitError(f"{repo.name}: cannot hash {relative}")
        contents[relative] = (digest, mode, blob.stdout.decode("ascii").strip())
    return RepoSnapshot(head, branch, tuple(parse_porcelain_v2(status.stdout)), index, contents)


def _pending_push_changes(
    repo: RepoState,
    config: SmartCommitConfig,
    rules: Sequence[SensitiveRule],
    submodule_paths: set[str],
) -> None:
    """Review every unpublished commit, including content later deleted again."""
    plan = repo.push_plan
    assert plan is not None
    remote = _run_prefixed(
        repo.path,
        [*plan.prefix, "ls-remote", plan.url, f"refs/heads/{plan.branch}"],
        config.timeout_seconds,
        ignore_github_token=plan.ignore_github_token,
    )
    if remote.returncode:
        raise SmartCommitError(f"{repo.name}: unable to inspect remote branch before pushing")
    repo.remote_hash = remote.stdout.split()[0] if remote.stdout.strip() else ""
    head = repo.snapshot.head if repo.snapshot else ""
    repo.push_required = bool(head) and head != repo.remote_hash
    if not repo.push_required:
        return
    revisions = ["rev-list", head]
    if repo.remote_hash:
        revisions.append(f"^{repo.remote_hash}")
    commits = _git_text(repo.path, revisions, timeout=config.timeout_seconds).splitlines()
    seen = set()
    for commit in commits:
        result = _run_git(
            repo.path,
            [
                "diff-tree",
                "--root",
                "-m",
                "--no-commit-id",
                "--name-status",
                "-r",
                "-z",
                "-M",
                commit,
            ],
            timeout=config.timeout_seconds,
            text=False,
        )
        if result.returncode:
            raise SmartCommitError(f"{repo.name}: unable to review pending commit {commit[:12]}")
        records = iter(result.stdout.split(b"\0"))
        for record in records:
            if not record:
                continue
            status = record.decode("ascii")[:1]
            original = os.fsdecode(next(records))
            path = os.fsdecode(next(records)) if status in {"R", "C"} else original
            change = Change(
                path, status, status + ".", status != "A", original if status == "R" else None
            )
            if change not in seen:
                seen.add(change)
                repo.push_classifications.append(classify_change(repo, change, submodule_paths))
            if not rules or status == "D":
                continue
            diff = _git_text(
                repo.path,
                [
                    "diff-tree",
                    "--root",
                    "-m",
                    "--no-commit-id",
                    "-p",
                    "--no-ext-diff",
                    "--no-color",
                    "--unified=0",
                    commit,
                    "--",
                    path,
                ],
                timeout=config.timeout_seconds,
            )
            for line_number, line in _added_lines(diff):
                for rule in rules:
                    if _rule_matches(rule, path, line):
                        hit = SensitiveHit(
                            path, line_number, rule.keyword, rule.category, line.strip()[:160]
                        )
                        if hit not in repo.push_sensitive_hits:
                            repo.push_sensitive_hits.append(hit)


def _may_need_push(repo: RepoState, config: SmartCommitConfig) -> bool:
    """Avoid changing the established no-op behavior for clean synchronized repos."""
    if repo.has_changes:
        return True
    if repo.snapshot is None or not repo.snapshot.head or not repo.remote_url:
        return False
    cached = _run_git(
        repo.path,
        ["rev-parse", "--verify", f"refs/remotes/origin/{repo.target_branch}"],
        timeout=config.timeout_seconds,
    )
    return cached.returncode != 0 or cached.stdout.strip() != repo.snapshot.head


def build_plan(
    root: Path,
    *,
    mode: str,
    config: SmartCommitConfig,
    push: bool,
    allow_delete: bool = False,
    allow_rename: bool = False,
    allow_sensitive: bool = False,
) -> SmartCommitPlan:
    repos = scan_repositories(discover_repositories(root, mode), config)
    submodule_paths = {repo.relative_path for repo in repos if not repo.is_main}
    rules = load_sensitive_rules(root)

    def enrich(repo: RepoState) -> RepoState:
        repo.classifications = [
            classify_change(repo, change, submodule_paths) for change in repo.changes
        ]
        if repo.errors:
            return repo
        repo.snapshot = _capture_snapshot(repo, config)
        if repo.snapshot.status != tuple(repo.changes):
            raise SmartCommitError(
                f"{repo.name}: worktree changed during planning; rerun smart-commit"
            )
        repo.sensitive_hits = scan_sensitive(repo, rules, config.timeout_seconds)
        if push and _may_need_push(repo, config):
            repo.push_plan = select_push_plan(repo, config)
        if push and not repo.errors and repo.push_plan is not None:
            _detect_remote_ahead(repo, config)
            _pending_push_changes(repo, config, rules, submodule_paths)
        _assert_snapshot(repo, config)
        return repo

    # Superproject and submodule Git state share administrative files. Planning
    # them concurrently can observe a sibling's transient index.lock.
    ordered = [enrich(repo) for repo in repos]
    return SmartCommitPlan(
        root=root,
        mode=mode,
        repos=ordered,
        config=config,
        allow_delete=allow_delete,
        allow_rename=allow_rename,
        allow_sensitive=allow_sensitive,
        push_requested=push,
    )


def _assert_snapshot(repo: RepoState, config: SmartCommitConfig) -> None:
    if repo.snapshot is None or _capture_snapshot(repo, config) != repo.snapshot:
        raise SmartCommitError(f"{repo.name}: worktree changed after planning; rerun smart-commit")
    state = RepoState(repo.name, repo.path, repo.relative_path, repo.is_main)
    _check_git_state(state, config)
    if state.errors:
        raise SmartCommitError(f"{repo.name}: {'; '.join(state.errors)}")


def _assert_staged_snapshot(repo: RepoState, config: SmartCommitConfig, skipped: set[str]) -> None:
    snapshot = repo.snapshot
    if snapshot is None:
        raise SmartCommitError(f"{repo.name}: missing reviewed snapshot")
    current = _capture_snapshot(repo, config)
    expected = dict(snapshot.index)
    for path, content in snapshot.contents.items():
        if content is None or not content[1]:
            expected.pop(path, None)
        else:
            expected[path] = (content[1], content[2], "0")
    for path in skipped:
        if snapshot.head:
            entry = _git_text(
                repo.path,
                ["ls-tree", "-z", snapshot.head, "--", path],
                timeout=config.timeout_seconds,
            )
            if entry:
                mode, _, oid = entry.split("\t", 1)[0].split()
                expected[path] = (mode, oid, "0")
                continue
        expected.pop(path, None)
    approved_paths = {change.path for change in snapshot.status}
    approved_paths.update(
        change.original_path for change in snapshot.status if change.original_path
    )
    unexpected = {change.path for change in current.status} - approved_paths
    if (
        current.head != snapshot.head
        or current.branch != snapshot.branch
        or current.contents != snapshot.contents
        or current.index != expected
        or unexpected
    ):
        raise SmartCommitError(
            f"{repo.name}: staged content changed after review; rerun smart-commit"
        )


def _stage_paths(repo: RepoState, paths: Sequence[str], config: SmartCommitConfig) -> None:
    # `git add --all -- <pathspec>` aborts with "pathspec did not match any files"
    # when a pathspec targets only a deletion that is already removed from the
    # index (for example a removal a user pre-staged with `git rm` before running
    # smart-commit). Such deletions are already staged, so skipping re-staging is
    # a correct no-op: `git commit` still records them. Scope the skip precisely
    # to staged deletions whose working-tree file is gone, so modifications,
    # additions, unstaged deletions and staged-then-recreated (resurrection)
    # cases keep the existing behaviour. Cross-platform: argument arrays only.
    pre_staged_deletions: set[str] = set()
    for item in repo.classifications:
        if item.mark == "OK" and _keeps_staged_deletion(repo, item.change, config):
            pre_staged_deletions.add(item.change.path)
    stageable = [path for path in paths if path not in pre_staged_deletions]
    for start in range(0, len(stageable), 100):
        chunk = list(stageable[start : start + 100])
        result = _run_git(repo.path, ["add", "--all", "--", *chunk], timeout=config.timeout_seconds)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "git add failed").strip()
            raise SmartCommitError(f"{repo.name}: git add failed: {detail}")


def _default_message(repo: RepoState, paths: Sequence[str]) -> str:
    if repo.is_main:
        has_cli = any(path.startswith("packages/one-context/") for path in paths)
        has_modules = any(path in {"features", "knowledge", "skills"} for path in paths)
        if has_cli and has_modules:
            return "feat(cli): add cross-platform smart commit and update submodules"
        if has_cli:
            return "feat(cli): add cross-platform smart commit"
        if has_modules:
            return "chore: bump submodule pointers"
        return "chore: update one-context workspace"
    if repo.name == "skills" or repo.relative_path == "skills":
        return "feat(skills): make smart commit cross-platform"
    if repo.name == "knowledge" or repo.relative_path == "knowledge":
        return "docs(knowledge): update knowledge base"
    if repo.name == "features" or repo.relative_path == "features":
        return "feat(features): update feature artifacts"
    return f"chore({repo.name}): update repository"


def _commit_repo(
    repo: RepoState,
    config: SmartCommitConfig,
    *,
    verify_snapshot: bool,
) -> RepoState:
    _assert_snapshot(repo, config)
    paths = [item.change.path for item in repo.classifications if item.mark == "OK"]
    if not paths:
        return repo
    _stage_paths(repo, paths, config)

    staged = _run_git(
        repo.path,
        ["diff", "--cached", "--no-renames", "--name-only", "-z"],
        timeout=config.timeout_seconds,
        text=False,
    )
    if staged.returncode != 0:
        raise SmartCommitError(f"{repo.name}: unable to inspect staged files")
    staged_paths = {os.fsdecode(item) for item in staged.stdout.split(b"\0") if item}
    skip_paths = {item.change.path for item in repo.classifications if item.mark == "SKIP"}
    skip_paths.update(
        item.change.original_path
        for item in repo.classifications
        if item.mark == "SKIP" and item.change.original_path
    )
    # SKIP paths (secrets, generated media, build artifacts) must never enter
    # the commit. If a prior index operation (e.g. a rename) left them staged,
    # unstage them now so the OK paths commit cleanly without the SKIP files.
    staged_skip = staged_paths & skip_paths
    if staged_skip:
        restore_args = (
            ["restore", "--staged", "--", *sorted(staged_skip)]
            if repo.snapshot and repo.snapshot.head
            else ["rm", "--cached", "--force", "--", *sorted(staged_skip)]
        )
        restored = _run_git(
            repo.path,
            restore_args,
            timeout=config.timeout_seconds,
        )
        if restored.returncode:
            raise SmartCommitError(f"{repo.name}: unable to unstage excluded paths")
        staged_paths -= staged_skip
    allowed_paths = set(paths)
    allowed_paths.update(
        item.change.original_path
        for item in repo.classifications
        if item.mark == "OK" and item.change.original_path
    )
    unexpected = staged_paths.difference(allowed_paths)
    if unexpected:
        raise SmartCommitError(
            f"{repo.name}: refusing to commit unexpected staged paths: {', '.join(sorted(unexpected))}"
        )
    if not staged_paths:
        return repo
    _assert_staged_snapshot(repo, config, staged_skip)
    reviewed_tree = _git_text(repo.path, ["write-tree"], timeout=config.timeout_seconds)

    message = config.messages.get(repo.name) or config.messages.get(repo.relative_path)
    message = message or _default_message(repo, sorted(staged_paths))
    if config.coauthors:
        trailers = "\n".join(f"Co-Authored-By: {author}" for author in config.coauthors)
        message = f"{message}\n\n{trailers}"
    result = _run_git(repo.path, ["commit", "-m", message], timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise SmartCommitError(f"{repo.name}: git commit failed: {result.stderr.strip()}")
    if (
        _git_text(repo.path, ["rev-parse", "HEAD^{tree}"], timeout=config.timeout_seconds)
        != reviewed_tree
    ):
        raise SmartCommitError(
            f"{repo.name}: commit hook changed reviewed content; local commit was created, "
            "review it before pushing"
        )
    repo.commit_hash = _git_text(repo.path, ["rev-parse", "HEAD"], timeout=config.timeout_seconds)
    repo.commit_message = message.splitlines()[0]
    repo.snapshot = _capture_snapshot(repo, config)
    return repo


def _rescan_main(plan: SmartCommitPlan) -> RepoState:
    main = next(repo for repo in plan.repos if repo.is_main)
    expected_links = {
        repo.relative_path: repo.commit_hash
        for repo in plan.repos
        if not repo.is_main and repo.commit_hash
    }
    current = _capture_snapshot(main, plan.config)
    before = main.snapshot
    if before is None:
        raise SmartCommitError("main repository has no reviewed snapshot")
    original_status = tuple(item for item in before.status if item.path not in expected_links)
    current_status = tuple(item for item in current.status if item.path not in expected_links)
    original_contents = {
        key: value for key, value in before.contents.items() if key not in expected_links
    }
    current_contents = {
        key: value for key, value in current.contents.items() if key not in expected_links
    }
    if (
        current.head != before.head
        or current.branch != before.branch
        or current.index != before.index
        or current_status != original_status
        or current_contents != original_contents
    ):
        raise SmartCommitError("main repository changed after planning; rerun smart-commit")
    for path, oid in expected_links.items():
        actual = _git_text(
            main.path / path, ["rev-parse", "HEAD"], timeout=plan.config.timeout_seconds
        )
        if actual != oid:
            raise SmartCommitError(f"main: unexpected submodule HEAD at {path}; rerun smart-commit")
    rescanned = _scan_repo(
        RepoState(main.name, main.path, main.relative_path, True),
        plan.config,
    )
    submodule_paths = {repo.relative_path for repo in plan.repos if not repo.is_main}
    rescanned.classifications = [
        classify_change(rescanned, change, submodule_paths) for change in rescanned.changes
    ]
    rescanned.sensitive_hits = main.sensitive_hits
    unsafe = [item for item in rescanned.classifications if item.mark not in {"OK", "SKIP"}]
    if rescanned.errors or unsafe or (rescanned.sensitive_hits and not plan.allow_sensitive):
        raise SmartCommitError(
            "main repository became unsafe after submodule commits; rerun planning"
        )
    if (
        any(item.change.status == "D" for item in rescanned.classifications)
        and not plan.allow_delete
    ):
        raise SmartCommitError("main repository contains a deletion after submodule commits")
    if (
        any(item.change.status == "R" for item in rescanned.classifications)
        and not plan.allow_rename
    ):
        raise SmartCommitError("main repository contains a rename after submodule commits")
    rescanned.push_plan = main.push_plan
    rescanned.push_required = main.push_required
    rescanned.remote_hash = main.remote_hash
    rescanned.push_classifications = main.push_classifications
    rescanned.push_sensitive_hits = main.push_sensitive_hits
    rescanned.snapshot = _capture_snapshot(rescanned, plan.config)
    return rescanned


def _push_and_verify(repo: RepoState, config: SmartCommitConfig) -> RepoState:
    plan = repo.push_plan
    if plan is None:
        raise SmartCommitError(f"{repo.name}: no successful push preflight is cached")
    _assert_snapshot(repo, config)
    assert repo.snapshot is not None
    reviewed_head = repo.snapshot.head
    result = _run_prefixed(
        repo.path,
        plan.command(dry_run=False, source=reviewed_head),
        config.timeout_seconds,
        ignore_github_token=plan.ignore_github_token,
    )
    if result.returncode != 0:
        raise SmartCommitError(
            f"{repo.name}: push failed: {(result.stderr or result.stdout).strip()}"
        )

    verify_command = [*plan.prefix, "ls-remote", plan.url, f"refs/heads/{plan.branch}"]
    verify = _run_prefixed(
        repo.path,
        verify_command,
        config.timeout_seconds,
        ignore_github_token=plan.ignore_github_token,
    )
    if verify.returncode != 0:
        raise SmartCommitError(f"{repo.name}: remote verification failed: {verify.stderr.strip()}")
    remote_hash = verify.stdout.split(maxsplit=1)[0] if verify.stdout.strip() else ""
    if remote_hash != reviewed_head:
        raise SmartCommitError(
            f"{repo.name}: remote hash {remote_hash or '<missing>'} does not match {reviewed_head}"
        )
    if plan.set_upstream and repo.branch:
        _git_text(
            repo.path,
            ["config", f"branch.{repo.branch}.remote", "origin"],
            timeout=config.timeout_seconds,
        )
        _git_text(
            repo.path,
            ["config", f"branch.{repo.branch}.merge", f"refs/heads/{plan.branch}"],
            timeout=config.timeout_seconds,
        )
    repo.pushed = True
    repo.pushed_hash = reviewed_head
    if not repo.commit_message:
        repo.commit_message = _git_text(
            repo.path,
            ["log", "-1", "--format=%s", reviewed_head],
            timeout=config.timeout_seconds,
        )
    return repo


def _verify_main_dependencies(plan: SmartCommitPlan, main: RepoState) -> None:
    """The newly published main history must reference published child commits."""
    known = {repo.relative_path: repo for repo in plan.repos if not repo.is_main}
    for child in discover_repositories(plan.root, "full")[1:]:
        if main.remote_hash:
            changed = _git_text(
                main.path,
                ["diff", "--name-only", main.remote_hash, "HEAD", "--", child.relative_path],
                timeout=plan.config.timeout_seconds,
            )
            if not changed:
                continue
        entry = _git_text(
            main.path,
            ["ls-tree", "HEAD", "--", child.relative_path],
            timeout=plan.config.timeout_seconds,
        )
        if not entry or not entry.startswith("160000 "):
            continue
        required = entry.split()[2]
        child = known.get(child.relative_path) or _scan_repo(child, plan.config)
        child.push_plan = child.push_plan or select_push_plan(child, plan.config)
        if child.errors or child.push_plan is None:
            raise SmartCommitError(f"main: cannot verify published submodule {child.relative_path}")
        transport = child.push_plan
        remote = _run_prefixed(
            child.path,
            [*transport.prefix, "ls-remote", transport.url, f"refs/heads/{transport.branch}"],
            plan.config.timeout_seconds,
            ignore_github_token=transport.ignore_github_token,
        )
        published = (
            remote.stdout.split()[0] if not remote.returncode and remote.stdout.strip() else ""
        )
        reachable = (
            _run_git(
                child.path,
                ["merge-base", "--is-ancestor", required, published],
                timeout=plan.config.timeout_seconds,
            )
            if published
            else None
        )
        if reachable is None or reachable.returncode:
            raise SmartCommitError(
                f"main: submodule {child.relative_path} commit {required[:12]} is not verified "
                "on its remote branch; publish it with --mode full first"
            )


def execute_plan(plan: SmartCommitPlan, *, push: bool) -> SmartCommitPlan:
    if not plan.safe:
        raise SmartCommitError("plan has safety blockers; resolve them and rerun")
    # Validate every repository before the first mutation, including clean
    # children whose already-created commits may still need publishing.
    for repo in plan.repos:
        _assert_snapshot(repo, plan.config)
        if push and (repo.has_changes or repo.push_required) and repo.push_plan is None:
            raise SmartCommitError(
                f"{repo.name}: rerun planning with --push before executing a push"
            )

    main = next(repo for repo in plan.repos if repo.is_main)
    children = [repo for repo in plan.changed_repos if not repo.is_main]
    workers = min(plan.config.jobs, max(1, len(children)))
    committed_children: dict[str, RepoState] = {}
    if children:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_commit_repo, repo, plan.config, verify_snapshot=True): repo.name
                for repo in children
            }
            for future in as_completed(futures):
                committed_children[futures[future]] = future.result()

    for index, repo in enumerate(plan.repos):
        if repo.name in committed_children:
            plan.repos[index] = committed_children[repo.name]

    if main.has_changes or any(repo.commit_hash for repo in committed_children.values()):
        refreshed_main = _rescan_main(plan)
        refreshed_main.push_plan = main.push_plan
        refreshed_main = _commit_repo(refreshed_main, plan.config, verify_snapshot=False)
        for index, repo in enumerate(plan.repos):
            if repo.is_main:
                plan.repos[index] = refreshed_main
                break

    if push:
        to_push = [
            repo
            for repo in plan.repos
            if not repo.is_main and (repo.commit_hash or repo.push_required)
        ]
        workers = min(plan.config.jobs, max(1, len(to_push)))
        pushed: dict[str, RepoState] = {}
        if to_push:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(_push_and_verify, repo, plan.config): repo.name for repo in to_push
                }
                for future in as_completed(futures):
                    pushed[futures[future]] = future.result()
            for index, repo in enumerate(plan.repos):
                if repo.name in pushed:
                    plan.repos[index] = pushed[repo.name]
        main = next(repo for repo in plan.repos if repo.is_main)
        if main.commit_hash or main.push_required:
            _verify_main_dependencies(plan, main)
            _push_and_verify(main, plan.config)
    return plan


def format_plan(plan: SmartCommitPlan, *, push: bool) -> str:
    lines = ["Smart Commit plan", f"Plan ID: {plan.plan_id}"]
    if not plan.active_repos:
        return "\n".join([*lines, "  no local changes"])
    for repo in plan.active_repos:
        lines.append(f"\n[{repo.name}] {repo.path}")
        for item in repo.classifications:
            original = f" <- {item.change.original_path}" if item.change.original_path else ""
            lines.append(
                f"  {item.mark:4} {item.change.status:2} {item.change.path}{original} — {item.reason}"
            )
        if repo.push_required:
            lines.append("  PUSH pending local commits")
        for item in repo.push_classifications:
            lines.append(
                f"  PUSH {item.mark:4} {item.change.status:2} {item.change.path} — {item.reason}"
            )
        for hit in [*repo.sensitive_hits, *repo.push_sensitive_hits]:
            lines.append(f"  SENSITIVE {hit.path}:{hit.line} [{hit.keyword}] {hit.context}")
        for error in repo.errors:
            lines.append(f"  ERROR {error}")
        if push and repo.push_plan:
            lines.append(
                f"  PUSH preflight=ok transport={repo.push_plan.transport} "
                f"branch={repo.push_plan.branch}"
            )
    blockers = plan.blockers
    lines.append("\nSafety: PASS" if not blockers else "\nSafety: BLOCKED")
    lines.extend(f"  - {blocker}" for blocker in blockers)
    return "\n".join(lines)


def format_result(plan: SmartCommitPlan) -> str:
    lines = ["Smart Commit result"]
    for repo in plan.repos:
        if not repo.commit_hash and not repo.pushed:
            continue
        push_status = "pushed+verified" if repo.pushed else "committed"
        lines.append(
            f"  {repo.name}: {(repo.commit_hash or repo.pushed_hash)[:12]} {repo.commit_message} ({push_status})"
        )
    if len(lines) == 1:
        lines.append("  no commits created")
    return "\n".join(lines)
