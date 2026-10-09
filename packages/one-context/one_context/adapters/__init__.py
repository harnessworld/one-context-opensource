"""
Tool-specific adapters (Cursor, Claude Code, OpenClaw, ...).

Canonical rules, skills, and context live under the repo ``knowledge/`` tree
and ``meta/*.yaml``.  Adapters translate that shared intent into formats each
tool understands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from one_context.adapters._report import ReportEntry


@dataclass(frozen=True)
class GeneratedFile:
    """One file produced by an adapter.

    The first three fields are required for backward compatibility; the
    rest carry optional metadata for the per-asset compat matrix report.
    Adapters that don't set them still work — defaults are conservative
    (``status="native"``, empty ``source_id``) and surfaced as such in
    ``adapt-report.json``.
    """

    rel_path: str
    """Path relative to the one-context root."""

    content: str
    """Full file content."""

    description: str
    """Human-readable explanation (shown in dry-run output)."""

    # — compat-matrix metadata (optional, all default-blank) —
    source_id: str = ""
    """Stable logical id of the canonical source (e.g. skill dir-name, agent id,
    workspace id). Empty means the adapter didn't bother; the report will
    use ``rel_path`` as a fallback id."""

    source_kind: str = ""
    """One of: skill | agent | profile | rule | overview | manifest | instructions."""

    target_kind: str = ""
    """Role of the generated file at the target. Defaults to ``source_kind``."""

    status: str = "native"
    """One of STATUS_NATIVE / STATUS_TRANSLATE / STATUS_AGGREGATE / STATUS_MANUAL
    / STATUS_UNSUPPORTED (see adapters._report). Defaults to native because
    legacy adapters emit their primary product; downgrade if/when an adapter
    actually drops capability."""

    trigger: str = ""
    """How the target tool loads this file (free-text, surface in report)."""

    limitations: tuple[str, ...] = ()
    """Why this isn't native / what's lost in translation. Tuple = hashable
    + immutable for frozen dataclass."""

    source_digest: str = ""
    """SHA-256 first 12 chars of canonical source content. ``""`` means
    adapter didn't compute it; ``--check`` will not detect upstream source
    edits for entries without digest."""

    source_path: str = ""
    """Path to the canonical source file relative to repo root, for traceability."""

    targets: tuple[str, ...] = ()
    """Adapters that consume a shared generated path; empty means its producer."""


class AdapterBase(ABC):
    """Base class for all tool adapters."""

    name: str = ""
    """Short machine-readable adapter name, e.g. ``cursor``."""

    supports_file_ref: bool = False
    """True if the tool can follow ``@file`` references at runtime."""

    supports_agent_skills: bool = False
    """True if the tool discovers the shared ``.agents/skills`` surface."""

    supports_agents_md: bool = False
    """True if the tool loads the project-root ``AGENTS.md`` surface."""

    @abstractmethod
    def generate(
        self,
        root: Path,
        workspace: dict[str, Any],
        context: dict[str, Any],
    ) -> list[GeneratedFile]:
        """Produce tool-specific configuration files.

        Parameters
        ----------
        root:
            one-context root directory.
        workspace:
            The raw workspace dict from ``meta/workspaces.yaml``.
        context:
            The full context dict returned by
            ``one_context.context.build_workspace_context``.
        """
        ...

    def generate_agents(
        self,
        root: Path,
        agents: list[dict[str, Any]],
        profiles_by_id: dict[str, dict[str, Any]],
    ) -> list[GeneratedFile]:
        """Produce per-agent tool-specific configuration files.

        Default implementation returns an empty list; adapters override this
        to emit agent-specific rule / config files that allow the AI tool to
        activate a named agent without any manual file references.

        Parameters
        ----------
        root:
            one-context root directory.
        agents:
            All agent dicts loaded from ``meta/agents.yaml``.
        profiles_by_id:
            Profile lookup dict keyed by lowercased profile id.
        """
        return []

    def generate_skills(
        self,
        root: Path,
        skills: list[Any],
    ) -> list[GeneratedFile]:
        """Produce tool-specific skill registration files.

        Default implementation returns an empty list; adapters override
        this to emit skill registration files that allow the AI tool to
        discover and use skills automatically.

        Parameters
        ----------
        root:
            one-context root directory.
        skills:
            All parsed ``SkillMeta`` objects from
            ``one_context.skills.discover_skills(root)``.
        """
        return []

    def generate_project_artifacts(
        self,
        root: Path,
        workspace_ids: list[str],
        agents: list[dict[str, Any]],
    ) -> list[GeneratedFile]:
        """Produce project-root integration files (e.g. ``CLAUDE.md``, OpenClaw manifest).

        Called once per ``onecxt adapt`` run after all workspace-level files
        and agent files are defined. Default: no extra files.
        """
        return []

    def unsupported_assets(self) -> list["ReportEntry"]:
        """Return placeholder report entries for asset kinds this adapter
        deliberately does **not** distribute.

        Examples: OpenCode and Codex adapters do not emit commands via onecxt,
        even though those platforms support them (the source lives elsewhere).
        Rules are never in this category — `.onecxt/RULES.md` is inlined into
        every adapter's startup surface, so no adapter declares rules
        unsupported. The returned entries get added to ``adapt-report.json``
        with ``status="unsupported"`` so consumers can see a complete
        capability matrix per target — not a silent skip.

        Default implementation returns an empty list (no declaration).
        """
        return []


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

ADAPTERS: dict[str, type[AdapterBase]] = {}


def discover_adapters() -> None:
    """Import every public adapter module in this package.

    One tool adapter lives in one module (``adapters/<name>.py``).  Keeping
    discovery here means adding a target does not also require editing the CLI
    command implementation, and prevents tool-specific code from accumulating
    in the registry or command layer.
    """
    package_prefix = f"{__name__}."
    public_modules: set[str] = set()
    for module_info in pkgutil.iter_modules(__path__):  # type: ignore[name-defined]
        module_name = module_info.name
        if module_info.ispkg or module_name.startswith("_"):
            continue
        public_modules.add(module_name)
        importlib.import_module(package_prefix + module_name)

    registered = set(ADAPTERS)
    if registered != public_modules:
        missing = sorted(public_modules - registered)
        extra = sorted(registered - public_modules)
        details = []
        if missing:
            details.append(f"unregistered public modules: {', '.join(missing)}")
        if extra:
            details.append(f"registered names without modules: {', '.join(extra)}")
        raise RuntimeError("Invalid adapter module layout (" + "; ".join(details) + ")")


def register(name: str):
    """Class decorator that registers an adapter under *name*."""

    def decorator(cls: type[AdapterBase]) -> type[AdapterBase]:
        expected_module = f"{__name__}.{name}"
        if cls.__module__ != expected_module:
            raise ValueError(
                f"Adapter {name!r} must be defined in {expected_module}, "
                f"not {cls.__module__}"
            )
        existing = ADAPTERS.get(name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"Adapter {name!r} is already registered by "
                f"{existing.__module__}.{existing.__name__}"
            )
        cls.name = name
        ADAPTERS[name] = cls
        return cls

    return decorator


def get_adapter(name: str) -> AdapterBase:
    """Instantiate a registered adapter by name."""
    discover_adapters()
    cls = ADAPTERS.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown adapter: {name!r}. "
            f"Available: {', '.join(sorted(ADAPTERS))}"
        )
    return cls()


def list_adapters() -> list[str]:
    """Return sorted list of registered adapter names."""
    discover_adapters()
    return sorted(ADAPTERS)
