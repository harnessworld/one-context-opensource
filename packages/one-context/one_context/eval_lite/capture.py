"""capture — 一键建案：满意产物 → case baseline。

D7 满意即标准（无 sign-off）：对满意产物跑一遍 assertions，
pass 的入 baseline 期望，fail/error 列出供 review（暴露缺口是 capture 职责）。
不调 cc（纯本地结构推断），真"一键建案"。

用法（cli 包装）：
  onecxt eval capture --skill <name> --from <feature根> --prompt "<显式调用prompt>"

产物：
  skills/<name>/evals/<case>/baseline.json   # pass 断言的期望值快照
  skills/<name>/evals/<case>/scenario.yaml   # case 配置
  skills/<name>/evals/<case>/prompts/explicit/01.md
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from one_context.eval.assertions import (
    AssertionContext,
    AssertionSpec,
    run_assertions,
)
from one_context.eval_lite.dimensions import load_dimensions
from one_context.eval_lite.schema import (
    Baseline,
    BaselineExpectation,
    CaseScenario,
    PromptSpec,
)


class CaptureError(ValueError):
    pass


@dataclass
class CaptureReport:
    """capture 结果摘要（cli 打印 + 供 review）。"""

    case_dir: Path
    passed: int
    failed: int
    errored: int
    # fail/error 的断言 id + 原因（暴露产物缺口，供人决定补产物还是移除断言）
    gaps: list[dict[str, Any]]
    baseline_path: Path
    scenario_path: Path
    prompt_path: Path
    # D8 负控零拒绝：capture 只落 explicit 一条，负控=0 → 标记（run 时 trigger 维度 INSUFFICIENT）
    needs_negatives: bool = True


def _flatten_assertions(dims: dict[str, dict[str, Any]]) -> list[tuple[str, AssertionSpec]]:
    """从维度模板收 (维度key, 断言) 列表——维度归属以 yaml 分组为准，
    不是 kind 默认映射（assertion_map 默认只作 fallback）。"""
    out: list[tuple[str, AssertionSpec]] = []
    for dim_key, spec in dims.items():
        for a in spec.get("assertions", []) or []:
            d = dict(a)
            d.setdefault("id", f"{dim_key}_{len(out)}")
            d.setdefault("kind", "file_exists")
            d.setdefault("blocking", True)
            out.append((dim_key, AssertionSpec(**{k: v for k, v in d.items()})))
    return out


def _expected_value(spec: AssertionSpec, result_detail: dict[str, Any]) -> Any:
    """从断言结果 detail 取实测值作期望（满意度即标准）。

    file_exists → True；json_field exists → True；数值类 → 实测值。
    简化：file 类存 True（期望"存在"），其余存 spec 原参数（capture 时已 pass）。
    """
    if spec.kind in ("file_exists",):
        return True
    if spec.kind == "file_absent" or spec.kind == "path_absent":
        return True
    if spec.kind == "text_contains" or spec.kind == "text_absent":
        return spec.needle
    if spec.kind == "json_field":
        return result_detail.get("actual", spec.value)
    return True


def capture_case(
    *,
    skill: str,
    from_dir: Path,
    prompt: str,
    case: str | None = None,
    dims_path: Path | None = None,
) -> CaptureReport:
    """对满意产物固化成 case baseline。

    from_dir: feature 根（含 production/ + remotion/，artifacts_base）。
    case: 默认 = from_dir 目录名。
    dims_path: 默认 skills/<skill>/eval.dimensions.yaml。
    """
    from_dir = from_dir.resolve()
    if not from_dir.is_dir():
        raise CaptureError(f"--from 目录不存在：{from_dir}")
    if not prompt.strip():
        raise CaptureError("--prompt 不能为空（首条 explicit 调用样本）")

    case = case or from_dir.name
    if dims_path is None:
        # 从 from_dir 向上找 repo 根（含 meta/repos.yaml），拼 skills/<skill>/eval.dimensions.yaml
        root = _repo_root(from_dir)
        dims_path = root / "skills" / skill / "eval.dimensions.yaml"
    dims = load_dimensions(dims_path)

    assertions = _flatten_assertions(dims)
    ctx = AssertionContext(
        sandbox_root=from_dir,
        artifacts_base=from_dir,   # 路径基准 = feature 根
        final_text="",
        stdout="",
        stderr_tail="",
        tool_calls=[],
        produced_paths=set(),
    )

    specs_only = [s for _, s in assertions]
    results = run_assertions(specs_only, ctx)

    passed, failed, errored = 0, 0, 0
    expectations: list[BaselineExpectation] = []
    gaps: list[dict[str, Any]] = []
    for (dim_key, spec), res in zip(assertions, results):
        # 维度归属以 yaml 分组为准；role 由 blocking 决定（blocking→gate）
        if res.status == "pass":
            passed += 1
            expectations.append(BaselineExpectation(
                assertion_id=spec.id,
                dimension=dim_key,  # type: ignore[arg-type]
                role="gate" if spec.blocking else "score",
                expected=_expected_value(spec, res.detail),
            ))
        else:
            failed += 1 if res.status == "fail" else 0
            errored += 1 if res.status == "error" else 0
            gaps.append({
                "id": spec.id,
                "kind": spec.kind,
                "dimension": dim_key,
                "status": res.status,
                "detail": res.detail,
                "hint": "产物缺此文件/字段：补产物 re-pin，或从该 skill eval.dimensions.yaml 移除该断言",
            })

    # 写 case 目录（基于 repo 根，不依赖 cwd）
    root = _repo_root(from_dir)
    case_dir = root / "skills" / skill / "evals" / case
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "prompts" / "explicit").mkdir(parents=True, exist_ok=True)

    git_sha = _git_sha(from_dir)
    baseline = Baseline(
        skill=skill,
        case=case,
        captured_at=str(int(time.time())),
        captured_from=str(from_dir),
        git_sha=git_sha,
        expectations=expectations,
    )
    baseline_path = case_dir / "baseline.json"
    baseline_path.write_text(
        baseline.model_dump_json(indent=2), encoding="utf-8"
    )
    import json
    # baseline.json 写成可读 JSON（pydantic model_dump_json 已是）
    _ = json.loads(baseline_path.read_text())  # 校验合法

    scenario = CaseScenario(
        skill=skill,
        case=case,
        target_path=str(from_dir),
        dimension_config=str(dims_path),
        prompts=[PromptSpec(category="explicit", prompt=prompt)],
    )
    scenario_path = case_dir / "scenario.yaml"
    scenario_path.write_text(
        yaml.safe_dump(scenario.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    prompt_path = case_dir / "prompts" / "explicit" / "01.md"
    prompt_path.write_text(prompt.strip() + "\n", encoding="utf-8")

    return CaptureReport(
        case_dir=case_dir,
        passed=passed,
        failed=failed,
        errored=errored,
        gaps=gaps,
        baseline_path=baseline_path,
        scenario_path=scenario_path,
        prompt_path=prompt_path,
        needs_negatives=True,  # capture 只落 explicit；负控待人补（D8 run 时强制）
    )


def _git_sha(repo: Path) -> str:
    """best-effort 取 from_dir 所在仓的 HEAD sha（D9.2 可复现）。"""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _repo_root(start: Path) -> Path:
    """从 start 向上找 repo 根（含 meta/repos.yaml）；找不到回退 start。"""
    p = start.resolve()
    for _ in range(10):
        if (p / "meta" / "repos.yaml").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start.resolve()
