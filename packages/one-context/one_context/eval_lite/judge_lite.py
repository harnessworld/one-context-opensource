"""judge_lite — LLM judge for the full run mode.

Rules first, judge second: the judge only covers dimensions that rules
cannot (subjective process/style aspects), and the full mode runs
quick → probe → judge in that order.

The judge rubric must be executable: the output schema is locked to
{dimension, score(0-1), evidence, rationale, verdict} and vague rubrics are
rejected at load time.

The judge is spawned via the agent CLI (override the binary with the
ONECXT_EVAL_AGENT_BIN environment variable) and forced to emit a single JSON
object. Any judge failure (no valid JSON) degrades the dimension to
INSUFFICIENT_EVIDENCE at the aggregation layer.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from one_context.eval_lite.trace import agent_bin


JUDGE_SCHEMA_KEYS = {"dimension", "score", "evidence", "rationale", "verdict"}
VALID_VERDICTS = {"PASS", "FAIL"}


class JudgeError(ValueError):
    pass


@dataclass
class JudgeResult:
    """A judge verdict for one subjective dimension."""

    dimension: str
    score: float           # 0..1
    evidence: str          # concrete evidence from the trace/artifacts supporting the verdict
    rationale: str         # why this score/verdict
    verdict: str           # PASS / FAIL (score >= threshold → PASS; caller sets the threshold)
    error: str = ""        # spawn/parse failure reason


def _judge_prompt(dimension: str, rubric: str, context_text: str) -> str:
    return f"""You are a skill-evaluation judge. Evaluate ONLY the [{dimension}] dimension.

Grading rubric (must be executable; grade FAIL if it is ambiguous):
{rubric}

Evidence context (trace summary of the agent run + artifacts):
---
{context_text}
---

Output exactly one JSON object — no markdown fences, no explanation:
{{"dimension": "{dimension}", "score": <float 0..1>, "evidence": "<concrete evidence from the trace/artifacts>",
  "rationale": "<why this score>", "verdict": "PASS or FAIL"}}

Score mapping: fully compliant = 1.0, minor deviation = 0.7-0.9, clear violation < 0.6.
The verdict reflects whether you consider this dimension passing."""


def judge_dimension(
    dimension: str,
    rubric: str,
    context_text: str,
    *,
    env: dict[str, str],
    workdir: str | None = None,
    timeout: int = 120,
    model: str | None = None,
) -> JudgeResult:
    """Spawn the agent CLI to judge one subjective dimension.

    The output schema is locked (parse failures return a JudgeResult with a
    non-empty error). An empty or non-executable rubric raises JudgeError
    (callers should reject such rubrics at load time).
    """
    if not rubric or not rubric.strip():
        raise JudgeError(f"dimension {dimension} is missing judge_rubric (vague rubrics are rejected)")
    if len(rubric.strip()) < 10:
        raise JudgeError(f"dimension {dimension} judge_rubric is too short to be an executable decision")

    prompt = _judge_prompt(dimension, rubric, context_text)
    cmd = [agent_bin(), "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", "1"]
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, cwd=workdir, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error=f"judge timed out after {timeout}s")
    if r.returncode != 0 and not r.stdout:
        return JudgeResult(dimension, 0.0, "", "", "FAIL",
                           error=f"{agent_bin()} exit={r.returncode}: {r.stderr[:200]}")

    # Extract the JSON text from the trailing result event of the stream-json output.
    result_text = ""
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "result":
            result_text = d.get("result", "") or ""
    return _parse_judge_json(dimension, result_text)


def _parse_judge_json(dimension: str, text: str) -> JudgeResult:
    """Extract the JSON object from the judge output, enforcing the schema.

    Markdown fences are stripped defensively.
    """
    if not text:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error="judge produced no output")
    # Strip ```json ... ``` fences (models occasionally add them).
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    # Extract the first {...} block.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error=f"judge output is not JSON: {stripped[:120]}")
    try:
        obj = json.loads(stripped[start:end + 1])
    except Exception as e:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error=f"judge JSON failed to parse: {e}")

    missing = JUDGE_SCHEMA_KEYS - set(obj.keys())
    if missing:
        return JudgeResult(dimension, 0.0, "", "", "FAIL",
                           error=f"judge JSON is missing fields {missing}")
    verdict = str(obj["verdict"]).strip().upper()
    if verdict not in VALID_VERDICTS:
        return JudgeResult(dimension, 0.0, "", "", "FAIL",
                           error=f"judge verdict is invalid: {verdict}")
    try:
        score = float(obj["score"])
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    return JudgeResult(
        dimension=dimension,
        score=score,
        evidence=str(obj.get("evidence", "")),
        rationale=str(obj.get("rationale", "")),
        verdict=verdict,
    )


def judge_verdict_from_score(score: float, threshold: float | None) -> str:
    """score → verdict: PASS if score >= threshold else FAIL. Default threshold 0.8."""
    th = threshold if threshold is not None else 0.8
    return "PASS" if score >= th else "FAIL"