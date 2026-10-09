"""Discover and parse skills from the ``skills/`` directory.

Each skill lives in ``skills/<name>/SKILL.md`` with optional YAML
frontmatter.  This module scans the directory, parses frontmatter,
and returns structured ``SkillMeta`` objects used by adapters to
generate tool-specific registration files.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

import json
import yaml


# ---------------------------------------------------------------------------
# Runtime install / uninstall helpers for Claude Code Skill tool
# ---------------------------------------------------------------------------

@dataclass
class InstallResult:
    """Outcome of a batch skill install operation."""

    installed: list[str]
    skipped: list[str]
    errors: list[str]


@dataclass
class UninstallResult:
    """Outcome of a batch skill uninstall operation."""

    removed: list[str]
    skipped: list[str]


def _claude_skills_home() -> Path:
    """Return the Claude Code runtime skills directory."""
    return Path.home() / ".claude" / "skills"


def _codex_skills_home() -> Path:
    """Return the Codex runtime skills directory.

    Codex (0.142.x) discovers user-scoped skills from ``$CODEX_HOME/skills``
    (defaulting to ``~/.codex/skills``). It does **not** scan a repo-level
    ``.agents/skills/`` directory, so skills must be installed here to be
    visible in the ``/`` menu.
    """
    codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(codex_home) / "skills"


# Maps a CLI ``--tool`` value to its runtime skills directory resolver.
_TOOL_SKILLS_HOME = {
    "claude": _claude_skills_home,
    "codex": _codex_skills_home,
}


# A runtime skill is an intentionally small, self-contained bundle.  Do not
# mirror arbitrary files from the source directory: the allowlist and
# conservative filters reduce the risk of local caches or credentials
# hitching a ride into a user's global runtime directory.
_SKILL_BUNDLE_DIRS = (
    "references",
    "playbooks",
    "scripts",
    "assets",
    "agents",
)

_SKILL_BUNDLE_CACHE_NAMES = {
    "__pycache__",
    "cache",
    "caches",
    "node_modules",
}

_SKILL_BUNDLE_SECRET_SUFFIXES = {
    ".env",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
}

_SKILL_BUNDLE_CREDENTIAL_NAMES = {
    "credential.json",
    "credential.yaml",
    "credential.yml",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "service-account.json",
    "service_account.json",
}

_SKILL_BUNDLE_DOCUMENT_SUFFIXES = {".md", ".markdown", ".mdx"}

_SKILL_BUNDLE_CREDENTIAL_PATTERN = re.compile(
    r"(?:^|[-_.])(?:credentials?|secrets?|tokens?|api[-_]?keys?|"
    r"service[-_]?accounts?)(?:$|[-_.])"
)

_SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _is_safe_skill_name(name: str) -> bool:
    """Return whether *name* is one portable, traversal-safe path component."""
    return bool(_SAFE_SKILL_NAME.fullmatch(name))


def _is_within_bundle(path: Path, bundle_root: Path) -> bool:
    """Return whether *path* resolves inside the canonical source bundle."""
    try:
        path.resolve(strict=True).relative_to(bundle_root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _looks_sensitive_bundle_file(path: Path) -> bool:
    """Conservatively reject likely credentials while preserving docs."""
    folded_name = path.name.casefold()
    suffixes = {suffix.casefold() for suffix in path.suffixes}
    if suffixes & _SKILL_BUNDLE_SECRET_SUFFIXES:
        return True
    if folded_name in _SKILL_BUNDLE_CREDENTIAL_NAMES:
        return True

    final_suffix = path.suffix.casefold()
    if final_suffix not in _SKILL_BUNDLE_DOCUMENT_SUFFIXES:
        if folded_name.startswith("id_"):
            return True
        if _SKILL_BUNDLE_CREDENTIAL_PATTERN.search(folded_name):
            return True

    # Filename filters cannot cover renamed key files. Inspect only a short
    # prefix and omit recognised PEM/OpenSSH private-key payloads.
    try:
        with path.open("rb") as handle:
            prefix = handle.read(4096)
    except OSError:
        return False
    return b"PRIVATE KEY-----" in prefix


def _read_manifest(dest_root: Path) -> dict[str, Any]:
    """Read ``.skills-manifest.json``; return empty dict on missing / bad JSON."""
    mf = dest_root / ".skills-manifest.json"
    if not mf.is_file():
        return {}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_manifest(dest_root: Path, manifest: dict[str, Any]) -> None:
    """Persist ``.skills-manifest.json`` atomically."""
    mf = dest_root / ".skills-manifest.json"
    mf.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _codex_sanitise_frontmatter(text: str) -> str:
    """Strip non-Codex-standard frontmatter fields before writing to ``~/.codex/skills/``.

    Codex (0.144.x) parses ``SkillFrontmatter`` with a strict set of
    recognised keys (``name``, ``description``, ``metadata``).  Unknown
    keys like ``triggers``, ``arguments``, ``short`` are **not** part of
    the schema and cause the skill to be skipped ("invalid SKILL.md").
    We keep only the standard keys; the canonical source in ``skills/``
    retains the full frontmatter.
    """
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    fm_text = text[3:end].strip()
    body = text[end + 3 :]
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return text  # unparsable — pass through as-is
    if not isinstance(fm, dict):
        return text
    clean: dict[str, Any] = {}
    for key in ("name", "description", "metadata"):
        if key in fm:
            clean[key] = fm[key]
    if not clean:
        # No standard keys at all — drop frontmatter entirely
        return body.lstrip("\n")
    new_fm = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{new_fm}\n---{body}"


def _copy_visible_tree(src: Path, dest: Path, bundle_root: Path) -> None:
    """Copy *src* recursively, omitting hidden/cache/secret entries and symlinks.

    Resource files are copied byte-for-byte (and retain their file mode), so
    Codex frontmatter sanitisation remains limited to the bundle's root
    ``SKILL.md``.  Symlinks are omitted to keep an allowlisted directory from
    escaping the canonical skill tree.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for entry in sorted(src.iterdir(), key=lambda path: path.name):
        folded_name = entry.name.casefold()
        if (
            entry.name.startswith(".")
            or folded_name in _SKILL_BUNDLE_CACHE_NAMES
            or entry.suffix.casefold() in {".pyc", ".pyo"}
            or entry.is_symlink()
            or not _is_within_bundle(entry, bundle_root)
        ):
            continue
        target = dest / entry.name
        if entry.is_dir():
            _copy_visible_tree(entry, target, bundle_root)
        elif entry.is_file() and not _looks_sensitive_bundle_file(entry):
            shutil.copy2(entry, target)


def _stage_skill_bundle(src_dir: Path, staged_dir: Path, tool: str) -> None:
    """Build a complete runtime bundle in *staged_dir*."""
    src_skill = src_dir / "SKILL.md"
    bundle_root = src_dir.resolve(strict=True)
    staged_dir.mkdir(parents=True, exist_ok=True)

    if tool == "codex":
        content = _codex_sanitise_frontmatter(
            src_skill.read_text(encoding="utf-8")
        )
        (staged_dir / "SKILL.md").write_text(content, encoding="utf-8")
    else:
        shutil.copy2(src_skill, staged_dir / "SKILL.md")

    for dirname in _SKILL_BUNDLE_DIRS:
        resource_dir = src_dir / dirname
        if (
            resource_dir.is_symlink()
            or not resource_dir.is_dir()
            or not _is_within_bundle(resource_dir, bundle_root)
        ):
            continue
        _copy_visible_tree(resource_dir, staged_dir / dirname, bundle_root)


def _replace_skill_bundle(staged_dir: Path, dest_dir: Path) -> None:
    """Swap a staged bundle into place, restoring the old one on failure."""
    if not dest_dir.exists():
        os.replace(staged_dir, dest_dir)
        return

    backup_root = Path(tempfile.mkdtemp(
        prefix=".onecxt-skill-backup-",
        dir=dest_dir.parent,
    ))
    backup_dir = backup_root / "bundle"
    os.replace(dest_dir, backup_dir)
    try:
        os.replace(staged_dir, dest_dir)
    except OSError:
        # The old managed bundle remains recoverable unless the second rename
        # succeeds.  Restore it before surfacing the install error.
        os.replace(backup_dir, dest_dir)
        try:
            backup_root.rmdir()
        except OSError:
            pass
        raise
    else:
        # The new bundle is committed at this point. Failure to clean the old
        # backup must not make the result claim installation failed (or skip a
        # required Codex manifest update) while the new destination is live.
        try:
            shutil.rmtree(backup_root)
        except OSError:
            pass


def install_skills(root: Path, names: list[str], tool: str = "claude", *, force: bool = False) -> InstallResult:
    """Copy project ``skills/<name>/`` bundles into the tool runtime dir.

    ``tool`` selects the destination: ``claude`` → ``~/.claude/skills/``,
    ``codex`` → ``$CODEX_HOME/skills/``. By default only copies when the
    target ``SKILL.md`` does not already exist; set ``force=True`` to replace
    the complete managed bundle and remove stale resources.

    A bundle contains its root ``SKILL.md`` plus visible descendants of the
    allowlisted ``references/``, ``playbooks/``, ``scripts/``, ``assets/``,
    and ``agents/`` directories. Other top-level files and directories are
    deliberately excluded.

    For ``codex`` tool, non-standard frontmatter fields in the root
    ``SKILL.md`` are stripped
    (Codex only recognises ``name``, ``description``, ``metadata``) and
    ``.skills-manifest.json`` is updated. Bundled resources are not rewritten.
    """
    from datetime import datetime, timezone

    installed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    dest_root = _TOOL_SKILLS_HOME[tool]()
    need_manifest = tool == "codex"
    manifest = _read_manifest(dest_root) if need_manifest else {}
    skills_block = manifest.setdefault("skills", {})

    for name in names:
        if not _is_safe_skill_name(name):
            errors.append(
                f"{name}: invalid skill name (expected one portable path component)"
            )
            continue

        src_dir = root / "skills" / name
        src = src_dir / "SKILL.md"
        if src_dir.is_symlink() or src.is_symlink():
            errors.append(f"{name}: source skill bundle may not be a symlink")
            continue
        if not src.is_file():
            errors.append(f"{name}: not found in skills/{name}/SKILL.md")
            continue

        dest_dir = dest_root / name
        dest = dest_dir / "SKILL.md"
        if dest.is_file() and not force:
            skipped.append(name)
            continue

        dest_exists = dest_dir.exists() or dest_dir.is_symlink()
        if dest_exists and (
            dest_dir.is_symlink()
            or not dest_dir.is_dir()
            or not dest.is_file()
        ):
            errors.append(
                f"{name}: destination exists but is not a managed skill "
                "bundle; refusing to replace"
            )
            continue

        staged_dir: Path | None = None
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            staged_dir = Path(tempfile.mkdtemp(
                prefix=".onecxt-skill-install-",
                dir=dest_root,
            ))
            _stage_skill_bundle(src_dir, staged_dir, tool)
            _replace_skill_bundle(staged_dir, dest_dir)
            staged_dir = None
        except OSError as exc:
            errors.append(f"{name}: install failed: {exc}")
            continue
        finally:
            if staged_dir is not None and staged_dir.exists():
                try:
                    shutil.rmtree(staged_dir)
                except OSError:
                    pass

        installed.append(name)

        # Update manifest entry for Codex
        if need_manifest and name not in skills_block:
            skills_block[name] = {
                "version": "1.0.0",
                "installedAt": datetime.now(timezone.utc).isoformat(
                    timespec="milliseconds"
                ).replace("+00:00", "Z"),
                "package": f"@onecxt/{name}",
                "path": str(dest_dir),
            }

    if need_manifest and installed:
        _write_manifest(dest_root, manifest)

    return InstallResult(installed=installed, skipped=skipped, errors=errors)


def uninstall_skills(names: list[str], tool: str = "claude") -> UninstallResult:
    """Remove ``<tool-runtime>/skills/<name>/`` directories.

    Directories that do not exist are reported as *skipped*.

    SAFETY: a runtime skills directory (``~/.claude/skills``,
    ``~/.codex/skills``) may contain unrelated projects that merely happen
    to live under it (e.g. an Electron app cloned as a sibling).  We only
    remove directories that are actually onecxt-installed skills — i.e.
    they contain a ``SKILL.md`` file.  Anything without ``SKILL.md`` is
    skipped, never ``rmtree``'d, so a stray project directory cannot be
    destroyed by a blind batch uninstall.

    For ``codex`` tool, also removes the entry from
    ``.skills-manifest.json``.
    """
    import sys

    removed: list[str] = []
    skipped: list[str] = []
    dest_root = _TOOL_SKILLS_HOME[tool]()
    need_manifest = tool == "codex"
    manifest = _read_manifest(dest_root) if need_manifest else {}
    skills_block = manifest.get("skills", {})

    for name in names:
        if not _is_safe_skill_name(name):
            print(
                f"skip: invalid skill name {name!r} (refusing path traversal)",
                file=sys.stderr,
            )
            skipped.append(name)
            continue

        dest_dir = dest_root / name
        if not dest_dir.exists():
            skipped.append(name)
            continue

        # Guard: refuse symlinks and anything that is not a real skill dir.
        if (
            dest_dir.is_symlink()
            or not dest_dir.is_dir()
            or not (dest_dir / "SKILL.md").is_file()
        ):
            print(
                f"skip: {dest_dir} (not a managed local skill directory; "
                f"refusing to delete)",
                file=sys.stderr,
            )
            skipped.append(name)
            continue

        shutil.rmtree(dest_dir)
        removed.append(name)
        # Remove manifest entry
        skills_block.pop(name, None)

    if need_manifest and removed:
        _write_manifest(dest_root, manifest)

    return UninstallResult(removed=removed, skipped=skipped)


def list_skills_install_status(root: Path, tool: str = "claude") -> dict[str, Any]:
    """Return project skills and their runtime installation status for *tool*.

    Only directories that actually contain a ``SKILL.md`` are counted as
    installed — a runtime skills dir may hold unrelated projects (e.g. an
    Electron app) that should never surface as "installed skills" or be
    fed into a batch uninstall.
    """
    project = [s.dir_name for s in discover_skills(root)]
    dest_root = _TOOL_SKILLS_HOME[tool]()
    installed = sorted(
        d.name for d in dest_root.iterdir()
        if (
            _is_safe_skill_name(d.name)
            and not d.is_symlink()
            and d.is_dir()
            and (d / "SKILL.md").is_file()
        )
    ) if dest_root.is_dir() else []
    return {"project": project, "installed": installed}


# ---------------------------------------------------------------------------
# SkillMeta & parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillMeta:
    """Parsed representation of a single ``SKILL.md``.

    Attributes
    ----------
    name:
        Human-readable skill name from frontmatter (falls back to
        directory name when absent).
    dir_name:
        Directory name under ``skills/``, e.g. ``sc``.
        Used as the stable unique identifier.
    frontmatter:
        Raw parsed YAML frontmatter dict (empty ``{}`` when no
        frontmatter is present).
    body:
        Markdown body after the closing ``---`` of frontmatter.
    source_path:
        Relative path such as ``skills/sc/SKILL.md``.
    source_dir:
        Relative path such as ``skills/sc/``.
    """

    name: str
    dir_name: str
    frontmatter: dict[str, Any]
    body: str
    source_path: str
    source_dir: str


def discover_skills(root: Path) -> list[SkillMeta]:
    """Scan ``root/skills/`` for ``SKILL.md`` files and parse them.

    Returns an empty list when the ``skills/`` directory does not
    exist.  Results are sorted by directory name for deterministic
    output across runs.

    Raises ``SkillsSubmoduleNotInitialized`` when ``.gitmodules`` declares
    ``skills`` as a submodule but the working tree's ``skills/`` directory
    holds no ``SKILL.md`` files — the classic signature of a fresh clone
    without ``git submodule update --init``.  This guard prevents
    ``onecxt adapt`` from silently emitting empty routing tables.
    """
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        if _skills_submodule_declared_but_uninitialized(root):
            from one_context.errors import SkillsSubmoduleNotInitialized

            raise SkillsSubmoduleNotInitialized()
        return []

    result: list[SkillMeta] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        meta = parse_skill_md(root, skill_md)
        if meta is not None:
            result.append(meta)

    if not result and _skills_submodule_declared_but_uninitialized(root):
        from one_context.errors import SkillsSubmoduleNotInitialized

        raise SkillsSubmoduleNotInitialized()
    return result


def _skills_submodule_declared_but_uninitialized(root: Path) -> bool:
    """True iff ``.gitmodules`` declares ``skills`` but ``skills/`` is empty.

    Empty here means: no entries besides an optional ``.git`` pointer file
    (the typical state of a checked-out submodule slot before ``--init``).
    """
    gitmodules = root / ".gitmodules"
    if not gitmodules.is_file():
        return False
    try:
        text = gitmodules.read_text(encoding="utf-8")
    except OSError:
        return False
    if '[submodule "skills"]' not in text:
        return False
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return True
    try:
        children = list(skills_dir.iterdir())
    except OSError:
        return False
    real_children = [c for c in children if c.name != ".git"]
    return len(real_children) == 0


def parse_skill_md(root: Path, skill_md: Path) -> SkillMeta | None:
    """Parse a single ``SKILL.md``: extract YAML frontmatter + body.

    Returns ``None`` only if the file cannot be read.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None

    dir_name = skill_md.parent.name
    source_path = skill_md.relative_to(root).as_posix()
    source_dir = skill_md.parent.relative_to(root).as_posix() + "/"

    if not text.startswith("---"):
        # No frontmatter — use entire text as body
        return SkillMeta(
            name=dir_name,
            dir_name=dir_name,
            frontmatter={},
            body=text.strip(),
            source_path=source_path,
            source_dir=source_dir,
        )

    # Find closing ---
    end = text.find("---", 3)
    if end == -1:
        # Unclosed frontmatter — treat entire text as body
        return SkillMeta(
            name=dir_name,
            dir_name=dir_name,
            frontmatter={},
            body=text.strip(),
            source_path=source_path,
            source_dir=source_dir,
        )

    fm_text = text[3:end].strip()
    body = text[end + 3 :].strip()

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}

    if not isinstance(fm, dict):
        fm = {}

    name = fm.get("name") or dir_name

    return SkillMeta(
        name=name,
        dir_name=dir_name,
        frontmatter=fm,
        body=body,
        source_path=source_path,
        source_dir=source_dir,
    )


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from a markdown string.

    If the string starts with ``---``, everything up to and including
    the second ``---`` is stripped.  Otherwise the text is returned
    unchanged.
    """
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3 :].strip()


def resolve_skill_body(root: Path, skill: SkillMeta) -> str:
    """Return the body of a skill, reading from disk when possible.

    Adapters that need to *inline* skill content should use this helper
    instead of manually reading ``skills/<name>/SKILL.md`` and stripping
    frontmatter.  It centralises the fallback logic (disk → cached meta)
    and guarantees identical behaviour across all adapters.

    Parameters
    ----------
    root:
        Project root (containing ``skills/``).
    skill:
        Parsed metadata from ``discover_skills``.

    Returns
    -------
    str
        Frontmatter-stripped SKILL.md body.
    """
    skill_path = root / skill.source_path
    if skill_path.is_file():
        return strip_frontmatter(skill_path.read_text(encoding="utf-8"))
    # Fallback to the already-parsed body stored in SkillMeta
    return skill.body


def render_skill_reference_body(root: Path, skill: SkillMeta) -> str:
    """Render a project-local thin-skill body with source-drift detection."""
    source = root / skill.source_path
    try:
        source_text = source.read_text(encoding="utf-8")
    except OSError:
        source_text = skill.body
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:12]
    return "\n".join([
        f"<!-- canonical-source: {skill.source_path}; digest: {digest} -->",
        f"# Skill: {skill.name}",
        "",
        "Before following this skill, read and follow the canonical file",
        f"`{skill.source_path}` from the repository root. It is authoritative.",
        "",
        f"@{skill.source_path}",
    ])
