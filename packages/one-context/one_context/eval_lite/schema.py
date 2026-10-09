"""eval_lite data contracts (pydantic).

Three-state verdicts, three run modes. This file is the single source of
truth for the data shapes used by capture/run/report.
"""
from __future__ import annotations

from typing import Any, Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# --- verdicts & run modes ---

Verdict = Literal["PASS", "FAIL", "INSUFFICIENT_EVIDENCE"]

# Three modes: quick (local spec checks, no agent) / probe (stream-json
# trigger verification) / full (real agent run + judge).
RunMode = Literal["quick", "probe", "full"]

# Evidence role: gate (short-circuit on failure) vs score (threshold-based).
EvidenceRole = Literal["gate", "score"]


# Seven dimensions: outcome + process + style + efficiency, plus the two
# maintenance surfaces, the trigger hypothesis, and extension checks.
DimensionKey = Literal[
    "outcome",        # artifacts exist + content contracts hold
    "process",        # trace toolchain vs the steps declared in SKILL.md
    "style",          # file structure / code style
    "efficiency",     # command counts / tokens / deltas
    "surface",        # name/description/instructions surface diff
    "trigger",        # trigger checks across the four prompt categories
    "extension",      # extension checks: build / smoke / cleanliness / permissions
]


class AssertionLink(BaseModel):
    """Where one assertion sits in the dimension matrix (kind → dimension →
    gate/score). Defaults come from assertion_map.py; dimensions.yaml can
    override them."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: str
    dimension: DimensionKey
    role: EvidenceRole
    # Threshold for role=score (below → FAIL); role=gate needs no threshold.
    threshold: float | None = None


class DimensionResult(BaseModel):
    """Aggregated result of one dimension.

    Gate dimensions: any failure → verdict FAIL (short-circuit).
    Score dimensions: score < threshold → FAIL, otherwise PASS.
    verdict = FAIL | INSUFFICIENT_EVIDENCE (mode cannot measure it) | PASS.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: DimensionKey
    role: EvidenceRole
    verdict: Verdict
    score: float | None = None            # 0..1 for score dimensions (unused by gates)
    threshold: float | None = None
    # Assertion/trigger details under this dimension (expandable in reports).
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class CaseVerdict(BaseModel):
    """Overall verdict for one case (three states); also used for rollups."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    dimensions: list[DimensionResult] = Field(default_factory=list)
    # Why this verdict (which dimension went red, what evidence is missing).
    rationale: str = ""


class RunRecord(BaseModel):
    """Reproducibility contract: missing git_sha (or agent version for
    probe/full) forces INSUFFICIENT_EVIDENCE.

    Written to skills/<name>/evals/<case>/__runs/<runId>.json; run history is
    kept and never pruned automatically.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str                          # <unix seconds>-<hex>
    skill: str
    case: str
    mode: RunMode
    git_sha: str                         # required; empty → INSUFFICIENT_EVIDENCE
    cc_version: str = ""                 # required for probe/full, optional for quick
    started_at: str = ""
    duration_ms: int = 0
    cost_usd: float | None = None
    verdict: CaseVerdict
    # Trigger results for the four prompt categories (probe/full only).
    trigger_probes: list[dict[str, Any]] = Field(default_factory=list)
    # Warning flags (e.g. stale negative controls); shown as badges in reports.
    warnings: list[str] = Field(default_factory=list)


# --- baseline (a captured satisfactory artifact becomes the standard) ---

class BaselineExpectation(BaseModel):
    """One expectation. The measured values of a satisfactory artifact ARE
    the standard (first-version imperfections accepted; tightening happens
    over time)."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: str                    # matches AssertionSpec.id
    dimension: DimensionKey
    role: EvidenceRole
    expected: Any                        # measured value (captured from the satisfactory artifact)
    # Numeric tolerance for regression scoring (e.g. ±0.2); usually None for contract assertions.
    tolerance: float | None = None
    # Reserved for future sign-off (not required in the current version).
    signed_off: bool = False
    signer: str = ""


class Baseline(BaseModel):
    """skills/<name>/evals/<case>/baseline.json — the frozen satisfactory artifact."""

    model_config = ConfigDict(extra="forbid")

    skill: str
    case: str
    captured_at: str = ""
    captured_from: str = ""              # directory of the satisfactory artifact
    git_sha: str = ""
    expectations: list[BaselineExpectation] = Field(default_factory=list)


# --- case scenario (generated by capture) ---

class PromptSpec(BaseModel):
    """One prompt of the regression sample set (the trigger-hypothesis spine).

    explicit/implicit/context = positive controls (expected to trigger);
    negative = negative control (expected NOT to trigger). A capture with
    zero negatives cannot become ready: the trigger dimension stays
    INSUFFICIENT_EVIDENCE until negatives are added.
    """

    model_config = ConfigDict(extra="forbid")

    category: Literal["explicit", "implicit", "context", "negative"]
    prompt: str
    # Tracking for stale negative controls.
    last_full_checked_at: str = ""


class CaseScenario(BaseModel):
    """skills/<name>/evals/<case>/scenario.yaml — generated by capture."""

    model_config = ConfigDict(extra="forbid")

    skill: str
    case: str
    target_path: str                     # the artifact directory under test
    dimension_config: str = ""           # points at eval.dimensions.yaml
    prompts: list[PromptSpec] = Field(default_factory=list)


# --- rollup (one line per skill@sha) ---

class SkillRollup(BaseModel):
    """The only first-screen conclusion for a solo author: "can this version
    ship?". Priority: FAIL > INSUFFICIENT > PASS."""

    model_config = ConfigDict(extra="forbid")

    skill: str
    git_sha: str
    # Mode-qualified: PASS@full / PASS@probe / PASS@quick / FAIL.
    best_mode: RunMode | None = None      # highest mode reached by this rollup
    verdict: Verdict
    case_verdicts: dict[str, Verdict] = Field(default_factory=dict)
    note: str = ""


def rollup_skill(cases: list[tuple[str, Verdict]]) -> Verdict:
    """Priority: FAIL > INSUFFICIENT_EVIDENCE > PASS.

    Reds are reported as reds — a system state never masks a failing case.
    cases=[(case_name, verdict), ...].
    """
    vs = [v for _, v in cases]
    if "FAIL" in vs:
        return "FAIL"
    if "INSUFFICIENT_EVIDENCE" in vs:
        return "INSUFFICIENT_EVIDENCE"
    return "PASS"


def insuff_if_unreproducible(record: RunRecord) -> Verdict:
    """A run missing git_sha — or, for probe/full, the agent CLI version — is
    not reproducible and returns INSUFFICIENT_EVIDENCE.

    quick mode tolerates an empty agent version; probe/full require it.
    """
    if not record.git_sha:
        return "INSUFFICIENT_EVIDENCE"
    if record.mode in ("probe", "full") and not record.cc_version:
        return "INSUFFICIENT_EVIDENCE"
    return record.verdict.verdict