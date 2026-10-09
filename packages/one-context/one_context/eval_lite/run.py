"""run — 三档评测执行。

quick:  不 spawn cc，对 <case>.target_path 重跑 baseline 断言，
        每条结果 vs baseline 期望 → 退化判定（B 语义，真正验"改好改坏"）。
probe:  spawn cc stream-json 跑四类 prompt，只验 skill 触发 y/n（trace.py）。
        触发假设脊椎（D8）：负控误触 / 正控未触 = trigger 维度 FAIL。
full:   git worktree 隔离 → spawn cc 产出 → quick 断言 + LLM judge 双轨。

probe 档只评 trigger 一维（D8 脊椎不附庸），其余维度 probe 不覆盖。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from one_context.eval.assertions import AssertionContext, AssertionSpec, run_assertions
from one_context.eval_lite.assertion_map import DEFAULT_DIM_WIN
from one_context.eval_lite.dimensions import load_dimensions
from one_context.eval_lite.trace import (
    CcSpawnError,
    NoAuthError,
    TriggerEvidence,
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
    """quick run 结果。"""

    run_record: RunRecord
    deltas: list[dict[str, Any]] = field(default_factory=list)  # 退化明细


@dataclass
class ProbeResult:
    """probe run 结果：只评 trigger 一维（D8 脊椎）。"""

    run_record: RunRecord
    evidence: list[dict[str, Any]] = field(default_factory=list)  # 每条 prompt 触发证据


def _load_case(case_dir: Path) -> tuple[CaseScenario, Baseline]:
    """读 scenario.yaml + baseline.json。"""
    scenario_path = case_dir / "scenario.yaml"
    baseline_path = case_dir / "baseline.json"
    if not scenario_path.is_file():
        raise RunError(f"缺 scenario.yaml：{scenario_path}")
    if not baseline_path.is_file():
        raise RunError(f"缺 baseline.json：{baseline_path}")
    scenario = CaseScenario(**yaml.safe_load(scenario_path.read_text(encoding="utf-8")))
    baseline = Baseline(**json.loads(baseline_path.read_text(encoding="utf-8")))
    return scenario, baseline


def _flat_specs(case_dir: Path, scenario: CaseScenario, baseline: Baseline) -> list[AssertionSpec]:
    """从维度模板收断言（与 capture 同法），但只取 baseline 收录的那批（已 sign-off 的）。

    D7：baseline 之外的断言（capture 时 fail/error 未入）在 quick 里不评——
    那批是 capture 阶段已知的产物缺口，不是退化的信号，避免误报。
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
                continue  # 未入 baseline 的不评（capture 缺口，非退化）
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
    """<unix秒>-<12hex>，时间排序友好。"""
    return f"{int(time.time())}-{Path('/tmp').stat().st_mtime_ns:012x}"


def quick_run(skill: str, case: str, *, skills_root: Path | None = None) -> QuickResult:
    """quick 档：重跑 baseline 断言 vs 期望值，判退化。

    语义 B：当前断言 pass → 维度 PASS；当前 fail 而 baseline期望(pass) → 退化 FAIL。
    门控维度任一退化 → case FAIL。
    """
    skills_root = skills_root or Path("skills")
    case_dir = skills_root / skill / "evals" / case
    scenario, baseline = _load_case(case_dir)

    target = Path(scenario.target_path)
    if not target.is_dir():
        raise RunError(f"target_path 不存在：{target}")

    specs = _flat_specs(case_dir, scenario, baseline)
    ctx = AssertionContext(
        sandbox_root=target, artifacts_base=target,
        final_text="", stdout="", stderr_tail="", tool_calls=[], produced_paths=set(),
    )
    results = run_assertions(specs, ctx)

    # 每条断言判定：当前 pass？
    id_to_dim = {e.assertion_id: e.dimension for e in baseline.expectations}
    id_to_role = {e.assertion_id: ("gate" if e.role == "gate" else "score") for e in baseline.expectations}
    dim_status: dict[str, list[tuple[str, bool]]] = {}  # dim -> [(assertion_id, currently_pass)]
    deltas: list[dict[str, Any]] = []

    for spec, res in zip(specs, results):
        dim = id_to_dim.get(spec.id)
        currently_pass = (res.status == "pass")
        dim_status.setdefault(dim, []).append((spec.id, currently_pass))
        if res.status != "pass":
            # baseline 当时 pass（入了 baseline），现在不 pass = 退化
            deltas.append({
                "assertion_id": spec.id,
                "dimension": dim,
                "role": id_to_role.get(spec.id),
                "current_status": res.status,
                "detail": res.detail,
                "regression": True,  # baseline=pass → now≠pass = 退化
            })

    # D6① 维度聚合：gate 维度任一不 pass → 维度 FAIL；score 看 ratio
    dim_results: list[DimensionResult] = []
    for dim, items in dim_status.items():
        role = items[0][1]  # placeholder, 取下面真实 role
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

    # D6② case verdict：任一 gate 维度 FAIL → FAIL
    any_gate_fail = any(d.role == "gate" and d.verdict == "FAIL" for d in dim_results)
    case_verdict_v: Verdict = "FAIL" if any_gate_fail else "PASS"
    rationale = ""
    failed_dims = [d.dimension for d in dim_results if d.verdict == "FAIL"]
    if failed_dims:
        rationale = f"门控维度退化：{','.join(failed_dims)}"

    run_record = RunRecord(
        run_id=_new_run_id(),
        skill=skill, case=case, mode="quick",
        git_sha=_git_sha(target),
        cc_version="",  # quick 不调 cc
        started_at=str(int(time.time())),
        duration_ms=0,
        verdict=CaseVerdict(
            verdict=case_verdict_v,
            dimensions=dim_results,
            rationale=rationale,
        ),
        warnings=[],
    )
    # D9.2 校验（quick 不要求 cc_version，但 git_sha 必须有；缺 → INSUFFICIENT_EVIDENCE）
    if not run_record.git_sha:
        run_record.verdict.verdict = "INSUFFICIENT_EVIDENCE"
        run_record.verdict.rationale = (run_record.verdict.rationale or "") + " 缺 git_sha（不可复现）"
    elif insuff_if_unreproducible(run_record) == "INSUFFICIENT_EVIDENCE":
        run_record.verdict.verdict = "INSUFFICIENT_EVIDENCE"

    # 写 __runs/<runId>.json（保留不滚动删，D3）
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
    """probe 档：spawn cc 跑四类 prompt，只验 skill 触发 y/n（D8 脊椎）。

    只评 trigger 一维。正控（explicit/implicit/context）期望触发、负控（negative）
    期望不触发。负控误触 / 正控未触发 → trigger FAIL → case FAIL。
    无负控 → D9.5 负控零拒绝 → INSUFFICIENT_EVIDENCE。
    无 key → 整体降级 INSUFFICIENT_EVIDENCE，不阻塞 quick（D8）。
    """
    skills_root = skills_root or Path("skills")
    case_dir = skills_root / skill / "evals" / case
    scenario, _baseline = _load_case(case_dir)
    repo_root = skills_root.resolve().parent  # cc cwd：读项目 CLAUDE.md + skills/

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

    # D9.5 负控零拒绝：无负控 → trigger 维度无证据 → INSUFFICIENT
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
                    summary="无负控 prompt（D8：capture 需补 negative case）",
                )],
                rationale="负控零拒绝：未补 negative prompt，trigger 维度无法判定",
            ),
            warnings=["NO_NEGATIVE"],
        )
        return _write(rec)

    # 逐条 spawn
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
            # 落 transcript（可复现 D9.2）
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            tp = transcripts_dir / f"{run_id}.{p.category}.ndjson"
            tp.write_text(stdout, encoding="utf-8")
            ev.transcript_path = str(tp)
        except NoAuthError:
            auth_failed = True
            ev = TriggerEvidence(
                category=p.category, prompt=p.prompt, triggered=False,
                error="无 ANTHROPIC key，probe 降级",
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
    # 无 key → 整体降级 INSUFFICIENT（D8，不阻塞 quick）
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
                    summary="无 key，probe 未执行（降级不阻塞 quick）",
                )],
                rationale="无 ANTHROPIC key",
            ),
            warnings=["NO_AUTH"],
        )
        return _write(rec)

    # 聚合 trigger 维度：负控误触 / 正控未触发 → FAIL（error 项不参与判定，记 warning）
    errors = [e for e in evidence if e.error]
    if errors:
        warnings.append("PROBE_ERRORS")
    neg_misfires = [e for e in evidence if e.category == "negative" and e.triggered and not e.error]
    pos_misses = [e for e in evidence if e.category in ("explicit", "implicit", "context")
                  and not e.triggered and not e.error]

    if neg_misfires:
        tri_verdict: Verdict = "FAIL"
        rationale = f"负控误触：{[e.category for e in neg_misfires]}"
    elif pos_misses:
        tri_verdict = "FAIL"
        rationale = f"正控未触发：{[e.category for e in pos_misses]}"
    else:
        tri_verdict = "PASS"
        rationale = ""
    case_v: Verdict = "FAIL" if tri_verdict == "FAIL" else "PASS"

    # summary 区分正控/负控语义（负控期望不触发，"触发数/总数" 反语义）
    pos_n = sum(1 for e in evidence if e.category in ("explicit", "implicit", "context") and not e.error)
    neg_n = sum(1 for e in evidence if e.category == "negative" and not e.error)
    trigger_summary = (f"正控 {pos_n - len(pos_misses)}/{pos_n} 触发, "
                       f"负控 {len(neg_misfires)}/{neg_n} 误触")

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
    # D9.2：probe 缺 cc_version → INSUFFICIENT
    if insuff_if_unreproducible(run_record) == "INSUFFICIENT_EVIDENCE":
        run_record.verdict.verdict = "INSUFFICIENT_EVIDENCE"
        run_record.verdict.rationale = (run_record.verdict.rationale or "") + " 缺 cc_version（不可复现）"
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
    """full run 结果：trigger 门控 + judge 主观维度。"""

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
    """从 cc 的 stream-json trace 抠工具调用序列 + 末段结果，作 judge context。

    ponytail: 限长防 prompt 爆；只取 tool_use 序列 + 末段 result 文本。
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
                        calls.append(f'文本: {t[:120]}')
        if d.get("type") == "result":
            result_text = d.get("result", "") or ""
    seq = " → ".join(calls[:40]) if calls else "(无工具调用)"
    return (f"目标 skill: {target_skill}\n工具调用序列:\n{seq}\n\n"
            f"末段结果:\n{result_text[:500]}")[:limit]


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
    """full 档：主仓 spawn cc 跑 skill 收 trace → trigger 门控 → judge 主观。

    不用 worktree 隔离（C-05 实跑推翻）：content-pipeline media(WAV)不入 git → worktree
    缺输入 cc 跑不动；CLAUDE_CONFIG_DIR 隔离让 cc 不识别项目 skill 被迫替换。两个假性问题
    盖过隔离价值，故 full 在主仓跑（cc 能识别 skill + 有 media，如 probe）。D9.4 过程验证
    模式由 trigger 门控承载短路（不重复 quick 客观断言）。

    顺序（D9.4：便宜门控先挡明显 FAIL 省 judge token）：
      1. trigger 门控：trace 验 skill 触发，未触发 → 短路不 judge
      2. judge 主观维度（process 等有 judge_rubric 的 score 维度）：锁 JSON rubric
    cc 在主仓跑可能改 tracked 文件，finally 用 before/after diff 差集记 cc 改动入
    warnings 供水猿处置（不自动 checkout，避免误删留材）。
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
                            summary="无 key，full 未执行（降级不阻塞 quick）")],
                rationale="无 ANTHROPIC key",
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
                            summary="无 explicit prompt，full 无法 spawn cc")],
                rationale="无 explicit prompt",
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

    # 跑前快照未提交改动，跑后差集 = cc 改的（排除已存在改动避免假阳性）
    try:
        _b = subprocess.run(["git", "diff", "--name-only"],
                            cwd=cc_cwd, capture_output=True, text=True, timeout=10)
        changed_before = set(l for l in _b.stdout.splitlines() if l.strip())
    except Exception:
        changed_before = set()

    try:
        env = dict(os.environ)
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json",
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
                        verdict="INSUFFICIENT_EVIDENCE", summary="cc spawn 超时")],
                    rationale=f"cc spawn 超时 {timeout}s"),
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

        # trigger 门控：未触发 → 短路（D9.4）
        trigger_v: Verdict = "PASS" if parsed["triggered"] else "FAIL"
        dim_results.append(DimensionResult(
            dimension="trigger", role="gate", verdict=trigger_v,
            evidence=trigger_probes,
            summary=f"explicit 触发={'是' if parsed['triggered'] else '否'}",
        ))
        if not parsed["triggered"]:
            case_v = "FAIL"
            rationale = "trigger 门控：cc 未触发目标 skill，过程无意义（D9.4 短路）"
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
                        summary=f"rubric 不可执行: {e}",
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
        # spawn cc / judge 以外的意外异常兜底（cc 跑废、trace 解析崩等）
        case_v = "INSUFFICIENT_EVIDENCE"
        rationale = f"full 意外异常: {e}"
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
        rec.verdict.rationale = (rec.verdict.rationale or "") + " 缺 cc_version（不可复现）"
    _write_run_record(case_dir, rec)
    return FullResult(run_record=rec, evidence=trigger_probes)
