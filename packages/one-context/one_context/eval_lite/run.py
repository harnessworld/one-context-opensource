"""run — evaluation execution across the three modes.

quick:  no agent spawn; re-runs the baseline assertions against
        <case>.target_path and compares every result with the baseline
        expectation → regression verdict (verifies "did I break it?").
probe:  spawns the agent CLI in stream-json mode across the four prompt
        categories and only verifies whether the skill triggered yes/no
        (trace.py). Trigger-hypothesis spine: a negative-control misfire or
        a missed positive control = trigger dimension FAIL.
full:   spawns the agent CLI in the workspace root to produce artifacts →
        quick assertions + LLM judge on subjective dimensions.

probe evaluates only the trigger dimension; the other dimensions are not
covered by probe.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from one_context.eval.assertions import AssertionContext, AssertionSpec, run_assertions
from one_context.eval_lite.assertion_map import DEFAULT_DIM_WIN
from one_context.eval_lite.dimensions import load_dimensions
from one_context.eval_lite.trace import (
    CcSpawnError,
    NoAuthError,
    TriggerEvidence,
    agent_bin,
    collect_cc_version,
    parse_skill_trigger,
    spawn_cc_probe,
)
from one_context.eval_lite.schema import (
    Baseline,
    CaseScenario,
    CaseVerdict,
    DimensionResult,
    RunRecord,
    Verdict,
    insuff_if_unreproducible,
)


class RunError(ValueError):
    pass


@dataclass
class QuickResult:
    """Result of a quick run."""

    run_record: RunRecord
    deltas: list[dict[str, Any]] = field(default_factory=list)  # regression details


@dataclass
class ProbeResult:
    """Result of a probe run: only the trigger dimension is evaluated."""

    run_record: RunRecord
    evidence: list[dict[str, Any]] = field(default_factory=list)  # per-prompt trigger evidence


def _load_case(case_dir: Path) -> tuple[CaseScenario, Baseline]:
    """Read scenario.yaml + baseline.json."""
    scenario_path = case_dir / "scenario.yaml"
    baseline_path = case_dir / "baseline.json"
    if not scenario_path.is_file():
        raise RunError(f"missing scenario.yaml: {scenario_path}")
    if not baseline_path.is_file():
        raise RunError(f"missing baseline.json: {baseline_path}")
    scenario = CaseScenario(**yaml.safe_load(scenario_path.read_text(encoding="utf-8")))
    baseline = Baseline(**json.loads(baseline_path.read_text(encoding="utf-8")))
    return scenario, baseline


def _flat_specs(case_dir: Path, scenario: CaseScenario, baseline: Baseline) -> list[AssertionSpec]:
    """Collect assertions from the dimension template (same method as
    capture), but only the ones recorded in the baseline.

    Assertions outside the baseline (the ones that failed/errored at capture
    time) are NOT evaluated in quick mode — they are known artifact gaps
    from the capture stage, not regression signals, and evaluating them
    would produce false alarms.
    """
    dims_path = Path(scenario.dimension_config)
    dims = load_dimensions(dims_path)
    expected_ids = {e.assertion_id for e in baseline.expectations}
    id_to_dim = {e.assertion_id: e.dimension for e in baseline.expectations}
    out: list[AssertionSpec] = []
    for dim_key, spec in dims.items():
        for a in spec.get("assertions", []) or []:
            d = dict(a)
            if d.get("id") not in expected_ids:
                continue  # not in the baseline → skip (capture gap, not a regression)
            d.setdefault("kind", "file_exists")
            d.setdefault("blocking", True)
            out.append(AssertionSpec(**{k: v for k, v in d.items() if k in AssertionSpec.model_fields}))
    return out


def _git_sha(at: Path) -> str:
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(at),
                              capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _new_run_id() -> str:
    """Return a sortable, filename-safe ID without filesystem dependencies."""
    return f"{int(time.time())}-{uuid4().hex[:12]}"


def quick_run(skill: str, case: str, *, skills_root: Path | None = None) -> QuickResult:
    """quick mode: re-run baseline assertions vs expectations, detect regressions.

    Semantics: current assertion passes → dimension PASS; current assertion
    fails while the baseline expected a pass → regression FAIL. Any gate
    dimension with a regression → case FAIL.
    """
    skills_root = skills_root or Path("skills")
    case_dir = skills_root / skill / "evals" / case
    scenario, baseline = _load_case(case_dir)

    target = Path(scenario.target_path)
    if not target.is_dir():
        raise RunError(f"target_path does not exist: {target}")

    specs = _flat_specs(case_dir, scenario, baseline)
    ctx = AssertionContext(
        sandbox_root=target, artifacts_base=target,
        final_text="", stdout="", stderr_tail="", tool_calls=[], produced_paths=set(),
    )
    results = run_assertions(specs, ctx)

    # Per-assertion check: does it pass right now?
    id_to_dim = {e.assertion_id: e.dimension for e in baseline.expectations}
    id_to_role = {e.assertion_id: ("gate" if e.role == "gate" else "score") for e in baseline.expectations}
    dim_status: dict[str, list[tuple[str, bool]]] = {}  # dim -> [(assertion_id, currently_pass)]
    deltas: list[dict[str, Any]] = []

    for spec, res in zip(specs, results):
        dim = id_to_dim.get(spec.id)
        currently_pass = (res.status == "pass")
        dim_status.setdefault(dim, []).append((spec.id, currently_pass))
        if res.status != "pass":
            # The baseline passed at capture time; it does not pass now = regression.
            deltas.append({
                "assertion_id": spec.id,
                "dimension": dim,
                "role": id_to_role.get(spec.id),
                "current_status": res.status,
                "detail": res.detail,
                "regression": True,
            })

    # Dimension aggregation: any non-pass in a gate dimension → FAIL; score uses ratio.
    dim_results: list[DimensionResult] = []
    for dim, items in dim_status.items():
        role = items[0][1]  # placeholder, replaced by the real role below
        role = id_to_role.get(items[0][0], "gate")
        all_pass = all(p for _, p in items)
        if role == "gate":
            verdict: Verdict = "PASS" if all_pass else "FAIL"
        else:
            ratio = sum(1 for _, p in items if p) / max(len(items), 1)
            verdict = "PASS" if ratio >= 0.9 else "FAIL"
        dim_results.append(DimensionResult(
            dimension=dim, role=role, verdict=verdict,
            score=None, summary=f"{sum(1 for _,p in items if p)}/{len(items)} pass",
        ))

    # Case verdict: any gate dimension FAIL → FAIL.
    any_gate_fail = any(d.role == "gate" and d.verdict == "FAIL" for d in dim_results)
    case_verdict_v: Verdict = "FAIL" if any_gate_fail else "PASS"
    rationale = ""
    failed_dims = [d.dimension for d in dim_results if d.verdict == "FAIL"]
    if failed_dims:
        rationale = f"gate dimension regression: {','.join(failed_dims)}"

    run_record = RunRecord(
        run_id=_new_run_id(),
        skill=skill, case=case, mode="quick",
        git_sha=_git_sha(target),
        cc_version="",  # quick does not spawn the agent
        started_at=str(int(time.time())),
        duration_ms=0,
        verdict=CaseVerdict(
            verdict=case_verdict_v,
            dimensions=dim_results,
            rationale=rationale,
        ),
        warnings=[],
    )
    # Reproducibility check (quick does not require cc_version but git_sha is
    # mandatory; missing → INSUFFICIENT_EVIDENCE).
    if not run_record.git_sha:
        run_record.verdict.verdict = "INSUFFICIENT_EVIDENCE"
        run_record.verdict.rationale = (run_record.verdict.rationale or "") + " missing git_sha (not reproducible)"
    elif insuff_if_unreproducible(run_record) == "INSUFFICIENT_EVIDENCE":
        run_record.verdict.verdict = "INSUFFICIENT_EVIDENCE"

    # Persist __runs/<runId>.json (append-only history, never auto-pruned).
    runs_dir = case_dir / "__runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_record.run_id}.json").write_text(
        run_record.model_dump_json(indent=2), encoding="utf-8"
    )

    return QuickResult(run_record=run_record, deltas=deltas)


def probe_run(
    skill: str,
    case: str,
    *,
    skills_root: Path | None = None,
    max_turns: int = 2,
    timeout: int = 180,
) -> ProbeResult:
    """probe mode: spawn the agent CLI across the four prompt categories and
    only verify skill triggering yes/no (the trigger-hypothesis spine).

    Only the trigger dimension is evaluated. Positive controls
    (explicit/implicit/context) must trigger; the negative control must NOT.
    A negative misfire or a missed positive control → trigger FAIL → case FAIL.
    No negative control at all → INSUFFICIENT_EVIDENCE.
    Missing credentials degrade the whole probe to INSUFFICIENT_EVIDENCE;
    quick mode is not blocked by that.
    """
    skills_root = skills_root or Path("skills")
    case_dir = skills_root / skill / "evals" / case
    scenario, _baseline = _load_case(case_dir)
    repo_root = skills_root.resolve().parent  # agent cwd: reads the project context + skills/

    prompts = scenario.prompts or []
    positives = [p for p in prompts if p.category in ("explicit", "implicit", "context")]
    negatives = [p for p in prompts if p.category == "negative"]

    run_id = _new_run_id()
    cc_version = collect_cc_version()
    started = int(time.time())

    def _write(rec: RunRecord) -> ProbeResult:
        runs_dir = case_dir / "__runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / f"{rec.run_id}.json").write_text(
            rec.model_dump_json(indent=2), encoding="utf-8"
        )
        return ProbeResult(run_record=rec, evidence=[])

    # Zero negative controls → no evidence for the trigger dimension.
    if not negatives:
        rec = RunRecord(
            run_id=run_id, skill=skill, case=case, mode="probe",
            git_sha=_git_sha(case_dir), cc_version=cc_version,
            started_at=str(started), duration_ms=0,
            verdict=CaseVerdict(
                verdict="INSUFFICIENT_EVIDENCE",
                dimensions=[DimensionResult(
                    dimension="trigger", role="gate",
                    verdict="INSUFFICIENT_EVIDENCE",
                    summary="no negative-control prompt (capture needs a negative case added)",
                )],
                rationale="zero negative controls: the trigger dimension cannot be judged",
            ),
            warnings=["NO_NEGATIVE"],
        )
        return _write(rec)

    # Spawn per prompt.
    evidence: list[TriggerEvidence] = []
    total_cost = 0.0
    total_duration = 0
    auth_failed = False
    transcripts_dir = case_dir / "__runs" / "transcripts"
    for p in prompts:
        try:
            stdout, _stderr = spawn_cc_probe(
                p.prompt, workdir=repo_root, max_turns=max_turns, timeout=timeout,
            )
            parsed = parse_skill_trigger(stdout, skill)
            ev = TriggerEvidence(
                category=p.category, prompt=p.prompt,
                triggered=parsed["triggered"],
                matched_raw=parsed["matched"],
                all_skill_calls=parsed["all_calls"],
                cost_usd=parsed["cost_usd"],
                duration_ms=parsed["duration_ms"],
                num_turns=parsed["num_turns"],
            )
            # Persist the transcript (reproducibility).
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            tp = transcripts_dir / f"{run_id}.{p.category}.ndjson"
            tp.write_text(stdout, encoding="utf-8")
            ev.transcript_path = str(tp)
        except NoAuthError:
            auth_failed = True
            ev = TriggerEvidence(
                category=p.category, prompt=p.prompt, triggered=False,
                error="no Anthropic credentials; probe degraded",
            )
        except CcSpawnError as e:
            ev = TriggerEvidence(
                category=p.category, prompt=p.prompt, triggered=False, error=str(e),
            )
        evidence.append(ev)
        if ev.cost_usd is not None:
            total_cost += ev.cost_usd
        if ev.duration_ms is not None:
            total_duration += ev.duration_ms

    warnings: list[str] = []
    # Missing credentials → degrade the whole run (does not block quick).
    if auth_failed:
        rec = RunRecord(
            run_id=run_id, skill=skill, case=case, mode="probe",
            git_sha=_git_sha(case_dir), cc_version=cc_version,
            started_at=str(started), duration_ms=total_duration,
            cost_usd=total_cost or None,
            trigger_probes=[_ev_dict(e) for e in evidence],
            verdict=CaseVerdict(
                verdict="INSUFFICIENT_EVIDENCE",
                dimensions=[DimensionResult(
                    dimension="trigger", role="gate",
                    verdict="INSUFFICIENT_EVIDENCE",
                    summary="no credentials; probe skipped (degraded, does not block quick)",
                )],
                rationale="missing Anthropic credentials",
            ),
            warnings=["NO_AUTH"],
        )
        return _write(rec)

    # Aggregate the trigger dimension: negative misfire / missed positive →
    # FAIL (error entries do not participate; they become warnings).
    errors = [e for e in evidence if e.error]
    if errors:
        warnings.append("PROBE_ERRORS")
    neg_misfires = [e for e in evidence if e.category == "negative" and e.triggered and not e.error]
    pos_misses = [e for e in evidence if e.category in ("explicit", "implicit", "context")
                  and not e.triggered and not e.error]

    if neg_misfires:
        tri_verdict: Verdict = "FAIL"
        rationale = f"negative-control misfire: {[e.category for e in neg_misfires]}"
    elif pos_misses:
        tri_verdict = "FAIL"
        rationale = f"positive control did not trigger: {[e.category for e in pos_misses]}"
    else:
        tri_verdict = "PASS"
        rationale = ""
    case_v: Verdict = "FAIL" if tri_verdict == "FAIL" else "PASS"

    # Summary distinguishes positive/negative semantics (for negatives,
    # "triggered/total" would be inverted).
    pos_n = sum(1 for e in evidence if e.category in ("explicit", "implicit", "context") and not e.error)
    neg_n = sum(1 for e in evidence if e.category == "negative" and not e.error)
    trigger_summary = (f"positives triggered {pos_n - len(pos_misses)}/{pos_n}, "
                       f"negatives misfired {len(neg_misfires)}/{neg_n}")

    run_record = RunRecord(
        run_id=run_id, skill=skill, case=case, mode="probe",
        git_sha=_git_sha(case_dir), cc_version=cc_version,
        started_at=str(started), duration_ms=total_duration,
        cost_usd=total_cost or None,
        trigger_probes=[_ev_dict(e) for e in evidence],
        verdict=CaseVerdict(
            verdict=case_v,
            dimensions=[DimensionResult(
                dimension="trigger", role="gate", verdict=tri_verdict,
                evidence=[_ev_dict(e) for e in evidence],
                summary=trigger_summary,
            )],
            rationale=rationale,
        ),
        warnings=warnings,
    )
    # Reproducibility: probe missing cc_version → INSUFFICIENT_EVIDENCE.
    if insuff_if_unreproducible(run_record) == "INSUFFICIENT_EVIDENCE":
        run_record.verdict.verdict = "INSUFFICIENT_EVIDENCE"
        run_record.verdict.rationale = (run_record.verdict.rationale or "") + " missing cc_version (not reproducible)"
    _write(run_record)
    return ProbeResult(run_record=run_record,
                       evidence=[_ev_dict(e) for e in evidence])


def _ev_dict(e: TriggerEvidence) -> dict[str, Any]:
    return {
        "category": e.category,
        "prompt": e.prompt,
        "triggered": e.triggered,
        "matched_raw": e.matched_raw,
        "cost_usd": e.cost_usd,
        "duration_ms": e.duration_ms,
        "error": e.error,
        "transcript_path": e.transcript_path,
    }


@dataclass
class FullResult:
    """Result of a full run: trigger gate + judge on subjective dimensions."""

    run_record: RunRecord
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _fmt_input(inp: Any) -> str:
    if isinstance(inp, dict):
        parts = []
        for k in ("skill", "command", "path", "file_path", "prompt"):
            if k in inp:
                v = str(inp[k]).replace("\n", " ")[:80]
                parts.append(f"{k}={v}")
        return ", ".join(parts) if parts else str(inp)[:80]
    return str(inp)[:80]


def _trace_summary_for_judge(ndjson_text: str, target_skill: str, limit: int = 2000) -> str:
    """Extract the tool-call sequence + trailing result from the agent's
    stream-json trace as judge context.

    Length-capped to keep the prompt small; only the tool_use sequence and
    the final result text are included.
    """
    import json as _json
    calls: list[str] = []
    result_text = ""
    for line in ndjson_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = _json.loads(line)
        except Exception:
            continue
        msg = d.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    calls.append(f"{b.get('name', '?')}({_fmt_input(b.get('input'))})")
                elif b.get("type") == "text":
                    t = (b.get("text") or "").strip()
                    if t:
                        calls.append(f'text: {t[:120]}')
        if d.get("type") == "result":
            result_text = d.get("result", "") or ""
    seq = " → ".join(calls[:40]) if calls else "(no tool calls)"
    return (f"target skill: {target_skill}\ntool call sequence:\n{seq}\n\n"
            f"final result:\n{result_text[:500]}")[:limit]


def _write_run_record(case_dir: Path, rec: RunRecord) -> None:
    runs_dir = case_dir / "__runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{rec.run_id}.json").write_text(
        rec.model_dump_json(indent=2), encoding="utf-8"
    )


def full_run(
    skill: str,
    case: str,
    *,
    skills_root: Path | None = None,
    max_turns: int = 6,
    timeout: int = 300,
) -> FullResult:
    """full mode: spawn the agent CLI in the workspace root, collect the
    trace → trigger gate → judge on subjective dimensions.

    Runs in the main workspace (not an isolated worktree): feature media and
    generated inputs that are not tracked by git would be missing from a
    worktree, and isolating the agent's config would hide the project's
    skills. The trigger gate carries the process-verification short-circuit
    (quick's objective assertions are not repeated).

    Order (cheap gates first, saving judge tokens):
      1. trigger gate: the trace must show the target skill triggered,
         otherwise short-circuit (no judge);
      2. judge the subjective score dimensions (those carrying judge_rubric)
         with the locked JSON rubric.
    The agent may modify tracked files, so a before/after `git diff --name-only`
    delta is recorded in warnings for manual review (no automatic checkout,
    to avoid discarding work).
    """
    import os
    import subprocess
    from one_context.eval_lite.judge_lite import (
        JudgeError, judge_dimension, judge_verdict_from_score,
    )

    skills_root = skills_root or Path("skills")
    case_dir = skills_root / skill / "evals" / case
    scenario, _baseline = _load_case(case_dir)
    repo_root = skills_root.resolve().parent
    dims = load_dimensions(Path(scenario.dimension_config))

    run_id = _new_run_id()
    cc_version = collect_cc_version()
    started = int(time.time())

    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not token:
        rec = RunRecord(
            run_id=run_id, skill=skill, case=case, mode="full",
            git_sha=_git_sha(case_dir), cc_version=cc_version,
            started_at=str(started), duration_ms=0,
            verdict=CaseVerdict(
                verdict="INSUFFICIENT_EVIDENCE",
                dimensions=[DimensionResult(dimension="trigger", role="gate",
                            verdict="INSUFFICIENT_EVIDENCE",
                            summary="no credentials; full skipped (degraded, does not block quick)")],
                rationale="missing Anthropic credentials",
            ),
            warnings=["NO_AUTH"],
        )
        _write_run_record(case_dir, rec)
        return FullResult(run_record=rec)

    pos = [p for p in (scenario.prompts or []) if p.category == "explicit"]
    if not pos:
        rec = RunRecord(
            run_id=run_id, skill=skill, case=case, mode="full",
            git_sha=_git_sha(case_dir), cc_version=cc_version,
            started_at=str(started),
            verdict=CaseVerdict(
                verdict="INSUFFICIENT_EVIDENCE",
                dimensions=[DimensionResult(dimension="trigger", role="gate",
                            verdict="INSUFFICIENT_EVIDENCE",
                            summary="no explicit prompt; full cannot spawn the agent")],
                rationale="no explicit prompt",
            ),
        )
        _write_run_record(case_dir, rec)
        return FullResult(run_record=rec)
    prompt = pos[0].prompt

    cost = 0.0
    duration = 0
    trigger_probes: list[dict[str, Any]] = []
    dim_results: list[DimensionResult] = []
    warnings: list[str] = []
    rationale = ""
    case_v: Verdict = "PASS"
    cc_cwd = str(repo_root)

    # Snapshot uncommitted changes before the run; the after-run delta is the
    # agent's change set (excluding pre-existing changes avoids false positives).
    try:
        _b = subprocess.run(["git", "diff", "--name-only"],
                            cwd=cc_cwd, capture_output=True, text=True, timeout=10)
        changed_before = set(l for l in _b.stdout.splitlines() if l.strip())
    except Exception:
        changed_before = set()

    try:
        env = dict(os.environ)
        cmd = [agent_bin(), "-p", prompt, "--output-format", "stream-json",
               "--verbose", "--max-turns", str(max_turns)]
        try:
            r = subprocess.run(cmd, cwd=cc_cwd, env=env,
                               capture_output=True, text=True, timeout=timeout)
            trace_ndjson = r.stdout
        except subprocess.TimeoutExpired:
            rec = RunRecord(
                run_id=run_id, skill=skill, case=case, mode="full",
                git_sha=_git_sha(case_dir), cc_version=cc_version,
                started_at=str(started), duration_ms=timeout * 1000,
                verdict=CaseVerdict(verdict="INSUFFICIENT_EVIDENCE",
                    dimensions=[DimensionResult(dimension="trigger", role="gate",
                        verdict="INSUFFICIENT_EVIDENCE", summary="agent spawn timed out")],
                    rationale=f"agent spawn timed out after {timeout}s"),
                warnings=["CC_TIMEOUT"],
            )
            _write_run_record(case_dir, rec)
            return FullResult(run_record=rec)

        tr_dir = case_dir / "__runs" / "transcripts"
        tr_dir.mkdir(parents=True, exist_ok=True)
        tr_path = tr_dir / f"{run_id}.full.ndjson"
        tr_path.write_text(trace_ndjson, encoding="utf-8")

        parsed = parse_skill_trigger(trace_ndjson, skill)
        if parsed.get("cost_usd") is not None:
            cost += parsed["cost_usd"]
        if parsed.get("duration_ms") is not None:
            duration += parsed["duration_ms"]
        trigger_probes = [{
            "category": "explicit", "prompt": prompt,
            "triggered": parsed["triggered"], "matched_raw": parsed["matched"],
            "cost_usd": parsed["cost_usd"], "duration_ms": parsed["duration_ms"],
            "transcript_path": str(tr_path),
        }]

        # Trigger gate: not triggered → short-circuit (no judge).
        trigger_v: Verdict = "PASS" if parsed["triggered"] else "FAIL"
        dim_results.append(DimensionResult(
            dimension="trigger", role="gate", verdict=trigger_v,
            evidence=trigger_probes,
            summary=f"explicit triggered={'yes' if parsed['triggered'] else 'no'}",
        ))
        if not parsed["triggered"]:
            case_v = "FAIL"
            rationale = "trigger gate: the agent did not invoke the target skill; the process verdict is meaningless (short-circuit)"
        else:
            context = _trace_summary_for_judge(trace_ndjson, skill)
            for dim_key, spec in dims.items():
                role = spec.get("role", "gate")
                rubric = spec.get("judge_rubric", "")
                modes = spec.get("modes_ok", [])
                if "full" not in modes or not rubric or role != "score":
                    continue
                threshold = spec.get("threshold")
                try:
                    jr = judge_dimension(
                        dim_key, rubric, context, env=env,
                        workdir=cc_cwd,
                    )
                except JudgeError as e:
                    dim_results.append(DimensionResult(
                        dimension=dim_key, role="score",
                        verdict="INSUFFICIENT_EVIDENCE",
                        summary=f"rubric not executable: {e}",
                    ))
                    warnings.append("JUDGE_RUBRIC_INVALID")
                    continue
                jv = jr.verdict if jr.error else judge_verdict_from_score(jr.score, threshold)
                dim_results.append(DimensionResult(
                    dimension=dim_key, role="score", verdict=jv,
                    score=jr.score, threshold=threshold,
                    evidence=[{"evidence": jr.evidence, "rationale": jr.rationale,
                               "error": jr.error}],
                    summary=(f"score={jr.score:.2f} verdict={jv}"
                             + (f" ({jr.error})" if jr.error else "")),
                ))
                if jr.error:
                    warnings.append("JUDGE_ERROR")
                elif jv == "FAIL":
                    case_v = "FAIL"
                    rationale = (rationale + "; " if rationale else "") + \
                                f"{dim_key} judge FAIL (score={jr.score:.2f})"
    except Exception as e:
        # Catch-all for unexpected failures (agent crashed, trace parsing blew up, etc.).
        case_v = "INSUFFICIENT_EVIDENCE"
        rationale = f"full run hit an unexpected exception: {e}"
        warnings.append("FULL_EXCEPTION")
        dim_results = dim_results or [DimensionResult(
            dimension="trigger", role="gate",
            verdict="INSUFFICIENT_EVIDENCE", summary=str(e))]
    finally:
        try:
            _a = subprocess.run(["git", "diff", "--name-only"],
                                cwd=cc_cwd, capture_output=True, text=True, timeout=10)
            changed_after = set(l for l in _a.stdout.splitlines() if l.strip())
        except Exception:
            changed_after = set()
        cc_changed = sorted(changed_after - changed_before)
        if cc_changed:
            warnings.append(f"CC_MODIFIED:{len(cc_changed)}")
            for c in cc_changed[:5]:
                warnings.append(f"  {c}")

    rec = RunRecord(
        run_id=run_id, skill=skill, case=case, mode="full",
        git_sha=_git_sha(case_dir), cc_version=cc_version,
        started_at=str(started), duration_ms=duration,
        cost_usd=cost or None, trigger_probes=trigger_probes,
        verdict=CaseVerdict(verdict=case_v, dimensions=dim_results, rationale=rationale),
        warnings=warnings,
    )
    if insuff_if_unreproducible(rec) == "INSUFFICIENT_EVIDENCE":
        rec.verdict.verdict = "INSUFFICIENT_EVIDENCE"
        rec.verdict.rationale = (rec.verdict.rationale or "") + " missing cc_version (not reproducible)"
    _write_run_record(case_dir, rec)
    return FullResult(run_record=rec, evidence=trigger_probes)