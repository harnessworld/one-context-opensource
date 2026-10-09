"""trace — parse the agent CLI's stream-json event stream.

The probe mode only checks whether the target skill was triggered (yes/no);
it does not judge the produced artifacts. This module:
  1. spawns the agent CLI (`<bin> -p --output-format stream-json --verbose`)
     to run one prompt;
  2. extracts Skill tool_use events from the NDJSON stream and decides
     whether the target skill was invoked (name matching is normalized);
  3. collects cost/duration/num_turns from the trailing result event; the
     agent CLI version is collected separately (it is not part of the stream).

Notes (empirically verified against the Claude Code CLI):
  - A skill trigger is a tool_use{name:"Skill",input.skill} block inside an
    assistant message's content[].
  - input.skill may carry a plugin namespace (e.g. `ns:skill-name`) so
    matching must normalize on the basename.
  - The trailing result event carries total_cost_usd / duration_ms /
    num_turns / session_id / usage.
  - The CLI version is not part of the stream-json envelope, so it is
    collected via a separate `<bin> --version` call.
  - Trigger-only runs use --max-turns N to cap cost (the Skill tool_use
    appears early, so truncation does not affect the yes/no answer).

The agent binary defaults to `claude` and can be overridden with the
ONECXT_EVAL_AGENT_BIN environment variable.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Environment variable used to override the agent CLI binary.
AGENT_BIN_ENV = "ONECXT_EVAL_AGENT_BIN"


def agent_bin() -> str:
    """Agent CLI binary used by probe/full runs (default: `claude`)."""
    return os.environ.get(AGENT_BIN_ENV, "claude") or "claude"


class NoAuthError(RuntimeError):
    """No ANTHROPIC_AUTH_TOKEN/API_KEY: probe degrades to INSUFFICIENT_EVIDENCE."""


class CcSpawnError(RuntimeError):
    """The agent CLI exited non-zero or timed out."""


@dataclass
class TriggerEvidence:
    """Trigger evidence collected for one probe prompt."""

    category: str                       # explicit/implicit/context/negative
    prompt: str
    triggered: bool                     # whether the target skill was invoked
    matched_raw: list[str] = field(default_factory=list)  # raw input.skill values that matched
    all_skill_calls: list[str] = field(default_factory=list)  # every Skill call (for debugging)
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    error: str = ""                     # spawn failure reason (non-empty → triggered forced False)
    transcript_path: str = ""           # on-disk transcript (for reproducibility)


def _skill_match(raw: str, target: str) -> bool:
    """Match input.skill (possibly namespaced) against the target skill name.

    Strict basename equality wins; substring containment is the fallback
    (triggers occasionally use aliases or abbreviations). Known ceiling:
    containment can misfire (e.g. `remotion` matching `remotion-pipelines`);
    tighten to strict equality once trigger coverage is narrower.
    """
    if not raw:
        return False
    basename = raw.split(":")[-1].strip()
    if basename == target:
        return True
    return basename in target or target in basename


def parse_skill_trigger(ndjson_text: str, target_skill: str) -> dict[str, Any]:
    """Pure parsing: NDJSON text → {matched, all_calls, cost, duration, num_turns}.

    No spawning, no external dependencies, unit-testable. Spawning lives in
    spawn_cc_probe.
    """
    matched: list[str] = []
    all_calls: list[str] = []
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    result_block: dict[str, Any] | None = None

    for line in ndjson_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        # Skill tool_use lives inside an assistant message's content[].
        msg = d.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "Skill":
                    raw = ""
                    inp = b.get("input")
                    if isinstance(inp, dict):
                        raw = str(inp.get("skill", "") or "")
                    all_calls.append(raw)
                    if _skill_match(raw, target_skill):
                        matched.append(raw)
        # Trailing result event (both subtype=success/error carry the summary).
        if d.get("type") == "result":
            result_block = d

    if result_block:
        cost_usd = result_block.get("total_cost_usd")
        duration_ms = result_block.get("duration_ms")
        num_turns = result_block.get("num_turns")

    return {
        "matched": matched,
        "all_calls": all_calls,
        "triggered": len(matched) > 0,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "result_subtype": (result_block or {}).get("subtype", ""),
    }


def collect_cc_version() -> str:
    """Collect the agent CLI version separately (it is not in the stream-json
    envelope). Best-effort: returns "" on any failure."""
    try:
        r = subprocess.run(
            [agent_bin(), "--version"], capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def spawn_cc_probe(
    prompt: str,
    *,
    workdir: Path,
    max_turns: int = 2,
    timeout: int = 180,
) -> tuple[str, str]:
    """Spawn the agent CLI in stream-json mode for one prompt.

    Returns (stdout NDJSON, stderr). Raises NoAuthError when no Anthropic
    credentials are configured (the caller degrades to
    INSUFFICIENT_EVIDENCE). max_turns truncation keeps cost down (default 2:
    the Skill tool_use appears early, so truncation does not affect the
    yes/no trigger answer).
    """
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not token:
        raise NoAuthError("missing ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY")
    cmd = [agent_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if max_turns and max_turns > 0:
        cmd += ["--max-turns", str(max_turns)]
    try:
        r = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise CcSpawnError(f"{agent_bin()} -p timed out after {timeout}s") from e
    # The CLI may print warnings on stderr even for a successful -p run;
    # stdout with NDJSON is what we parse.
    return r.stdout, r.stderr