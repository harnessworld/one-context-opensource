"""Adapt-report model + disk I/O for ``adapt-report.json``.

Each adapter produces ``GeneratedFile`` objects with optional metadata
(``source_id``, ``source_kind``, ``status``, ``trigger``, ``limitations``).
This module aggregates them into a single machine-readable report at the
repo root, plus tracks per-file disk status (``written`` / ``skipped_user_edited``
/ ``cached`` / ``dry_run``) from the emit-loop.

Schema lives at ``features/core/adapter-compat-matrix/spec.md`` AC1.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── status enums (string literals, kept loose for forward-compat) ─────────

# What the platform/adapter reports about an asset's mapping (capability matrix)
STATUS_NATIVE = "native"
STATUS_TRANSLATE = "translate"
STATUS_AGGREGATE = "aggregate"
STATUS_MANUAL = "manual"
STATUS_UNSUPPORTED = "unsupported"

# What the emit-loop reports about the on-disk file (per-run)
FILE_WRITTEN = "written"
FILE_CACHED = "cached"
FILE_SKIPPED_USER_EDITED = "skipped_user_edited"
FILE_DRY_RUN = "dry_run"
FILE_MISSING = "missing"  # reported by --check missing file
FILE_STALE = "stale"      # reported by --check content mismatch
FILE_USER_EDITED = "user_edited"  # reported by --check: file lacks marker


# ── digest helper ─────────────────────────────────────────────────────────

def source_digest(content: str | bytes) -> str:
    """Return first 12 chars of SHA-256 hex digest of canonical source content.

    Operational definition: ``content`` MUST be the canonical-source file
    body that the adapter read from (e.g. ``skills/<x>/SKILL.md`` text),
    NOT the generated output. This lets ``--check`` detect upstream source
    edits even if the adapter would produce byte-identical output.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:12]


# ── report dataclass ──────────────────────────────────────────────────────

@dataclass
class ReportEntry:
    source_id: str
    source_kind: str  # skill | agent | profile | rule | overview
    target: str       # primary consuming adapter (compatibility field)
    target_kind: str  # file role: agent | skill | instructions | rule | manifest | overview
    status: str       # one of STATUS_*
    path: str         # generated file path relative to repo root
    trigger: str = ""
    limitations: list[str] = field(default_factory=list)
    source_digest: str = ""
    file_status: str = ""  # one of FILE_*; filled by emit-loop
    source_path: str = ""  # canonical source file (relative to repo root), for traceability
    targets: list[str] = field(default_factory=list)
    """All consuming adapters; ``target`` remains the primary for compatibility."""


@dataclass
class AdaptReport:
    version: str = "1"
    generated_at: str = ""
    root: str = "."
    adapters: list[str] = field(default_factory=list)
    entries: list[ReportEntry] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    """Per-adapter capability probe results (AC8). Empty when probe was not
    run / not implemented for the adapter."""

    def add(self, entry: ReportEntry) -> None:
        if not entry.targets and entry.target:
            entry.targets.append(entry.target)
        self.entries.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "root": self.root,
            "adapters": self.adapters,
            "probes": self.probes,
            "entries": [asdict(e) for e in self.entries],
        }


# ── I/O ───────────────────────────────────────────────────────────────────

REPORT_FILENAME = "adapt-report.json"


def save_report(root: Path, report: AdaptReport) -> Path:
    """Write ``adapt-report.json`` at *root*. Returns the written path."""
    report.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report.root = "."
    target = root / REPORT_FILENAME
    target.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def load_report(root: Path) -> AdaptReport | None:
    """Return the saved report or ``None`` if absent/malformed."""
    target = root / REPORT_FILENAME
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries: list[ReportEntry] = []
    for raw_entry in raw.get("entries", []):
        entry = ReportEntry(**raw_entry)
        if not entry.targets and entry.target:
            entry.targets.append(entry.target)
        entries.append(entry)
    return AdaptReport(
        version=raw.get("version", "1"),
        generated_at=raw.get("generated_at", ""),
        root=raw.get("root", "."),
        adapters=raw.get("adapters", []),
        entries=entries,
        probes=raw.get("probes", []),
    )


# ── Capability probe (AC8) ────────────────────────────────────────────────

@dataclass
class ProbeResult:
    """Result of probing one CLI for its current version + discovery rules.

    Stored on the ``AdaptReport.adapters`` field as a side-channel
    (key = adapter name → result dict) so consumers can see whether the
    target CLI was actually installed when the report was generated.
    Probing is best-effort — failures must NOT block generation.
    """

    adapter: str
    version: str = ""
    available: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "version": self.version,
            "available": self.available,
            "error": self.error,
        }


# CLI binary → adapter mapping for probes. ``claude`` binary covers
# claude_code; the other names currently match their binaries. Adapters
# without a stable public CLI mapping return a non-blocking unavailable result.
_CLI_BY_ADAPTER: dict[str, str] = {
    "opencode": "opencode",
    "claude_code": "claude",
    "codex": "codex",
    "pi": "pi",
    # Add more when their CLI stabilizes:
    # "cursor": "cursor-cli",
    # "openclaw": "openclaw",
    # "hermes": "hermes",
}


def probe_cli(adapter_name: str) -> ProbeResult:
    """Best-effort probe of the target CLI's installed version.

    Returns ``ProbeResult(available=False, error=...)`` on any failure.
    Never raises — capability probe failures must degrade gracefully.
    """
    import shutil
    import subprocess

    binary = _CLI_BY_ADAPTER.get(adapter_name)
    if not binary:
        return ProbeResult(
            adapter=adapter_name,
            available=False,
            error=f"no probe mapping for adapter {adapter_name!r}",
        )
    if not shutil.which(binary):
        return ProbeResult(
            adapter=adapter_name,
            available=False,
            error=f"binary {binary!r} not on PATH",
        )
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return ProbeResult(
                adapter=adapter_name,
                available=False,
                error=f"{binary} --version exit {proc.returncode}: {proc.stderr.strip()[:120]}",
            )
        return ProbeResult(
            adapter=adapter_name,
            version=proc.stdout.strip() or proc.stderr.strip(),
            available=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return ProbeResult(
            adapter=adapter_name,
            available=False,
            error=str(exc)[:200],
        )
