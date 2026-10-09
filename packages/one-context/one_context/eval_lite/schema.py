"""eval_lite 数据契约（pydantic）。

设计依据 tech_design D6/D7/D9/D10。三态 verdict，三档 run。
本文件是上层 capture/run/report 的唯一数据形状来源，先定死再写胶水。
"""
from __future__ import annotations

from typing import Any, Literal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# --- verdict & run 档位（D6② 三态 / D4 三档）---

Verdict = Literal["PASS", "FAIL", "INSUFFICIENT_EVIDENCE"]

# 三档：quick(无cc 规格底线) / probe(stream-json 验触发) / full(worktree+judge)
RunMode = Literal["quick", "probe", "full"]

# 证据角色：门控(短路) vs 评分(阈值)
EvidenceRole = Literal["gate", "score"]


# 七维 + 维护面 + 触发假设 + 扩展检查（D4 矩阵）
DimensionKey = Literal[
    "outcome",        # 结果目标：artifacts 存在+内容契约
    "process",        # 过程目标：trace 工具链 vs SKILL.md 步骤
    "style",          # 风格目标：文件结构/代码风格
    "efficiency",     # 效率目标：命令数/token/delta
    "surface",        # 两个维护面：name/description/instructions diff
    "trigger",        # 触发假设：四类 prompt 触发判定
    "extension",      # 六类扩展检查：构建/冒烟/清洁度/权限
]


class AssertionLink(BaseModel):
    """一条断言在维度矩阵里的归属（C-06 断言→维度→门控/评分 对照）。

    由 assertion_map.py 提供 kind 默认映射，dimensions.yaml 可覆盖。
    """

    model_config = ConfigDict(extra="forbid")

    assertion_id: str
    dimension: DimensionKey
    role: EvidenceRole
    # role=score 时的阈值（<阈 FAIL）；role=gate 无需 threshold
    threshold: float | None = None


class DimensionResult(BaseModel):
    """D6① 一个维度的聚合结果。

    gate 维度：failure>0 → verdict FAIL 短路。
    score 维度：score < threshold → FAIL，否则 PASS。
    verdict = FAIL | INSUFFICIENT_EVIDENCE(该档测不了此维度) | PASS。
    """

    model_config = ConfigDict(extra="forbid")

    dimension: DimensionKey
    role: EvidenceRole
    verdict: Verdict
    score: float | None = None            # 评分维度 0..1（gate 不用）
    threshold: float | None = None
    # 该维度下的断言/触发明细（可在 report 二级展开）
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class CaseVerdict(BaseModel):
    """D6② 单 case 总判定 + D6③ rollup 用。三态。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    dimensions: list[DimensionResult] = Field(default_factory=list)
    # 为什么这个 verdict（FAIL 列哪个维度红、INSUFFICIENT 列缺什么）
    rationale: str = ""


class RunRecord(BaseModel):
    """D9.2 可复现契约：缺 sha+cc_version → 强制 INSUFFICIENT_EVIDENCE。

    写入 skills/<name>/evals/<case>/__runs/<runId>.json，保留不滚动删（D3）。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str                          # <unix秒>-<6hex>
    skill: str
    case: str
    mode: RunMode
    git_sha: str                         # 必填，空 → INSUFFICIENT_EVIDENCE
    cc_version: str = ""                 # probe/full 必填，quick 可空
    started_at: str = ""
    duration_ms: int = 0
    cost_usd: float | None = None
    verdict: CaseVerdict
    # 触发假设的四类 prompt 结果（probe/full 才有，quick 空）
    trigger_probes: list[dict[str, Any]] = Field(default_factory=list)
    # 警告旗（D8 STALE_NEGATIVE / D9.5 SAMPLE_STAGNANT），report 二级徽章
    warnings: list[str] = Field(default_factory=list)


# --- baseline（D7 满意即标准，无 sign-off）---

class BaselineExpectation(BaseModel):
    """一条期望值。满意产物实测值即标准（接受首版瑕疵，D9.5 自收紧兜底）。"""

    model_config = ConfigDict(extra="forbid")

    assertion_id: str                    # 对应 AssertionSpec.id
    dimension: DimensionKey
    role: EvidenceRole
    expected: Any                        # 实测值（capture 时从满意产物量出）
    # 回归档数值容差（如 ±0.2）；契约档通常 None
    tolerance: float | None = None
    # v3 扩展位（MVP 不填不强制，D7）
    signed_off: bool = False
    signer: str = ""


class Baseline(BaseModel):
    """skills/<name>/evals/<case>/baseline.json — 满意产物固化。"""

    model_config = ConfigDict(extra="forbid")

    skill: str
    case: str
    captured_at: str = ""
    captured_from: str = ""              # 满意产物目录
    git_sha: str = ""
    expectations: list[BaselineExpectation] = Field(default_factory=list)


# --- case scenario（capture 生成）---

class PromptSpec(BaseModel):
    """回归样本集四类 prompt（D8 触发假设脊椎）。

    explicit/implicit/context = 正控（期望触发）；negative = 负控（期望未触发）。
    capture 零负控 → 拒绝置 ready（D9.5 INSUFFICIENT_EVIDENCE）。
    """

    model_config = ConfigDict(extra="forbid")

    category: Literal["explicit", "implicit", "context", "negative"]
    prompt: str
    # 负控过期追踪（D8 STALE_NEGATIVE）
    last_full_checked_at: str = ""


class CaseScenario(BaseModel):
    """skills/<name>/evals/<case>/scenario.yaml — capture 生成。"""

    model_config = ConfigDict(extra="forbid")

    skill: str
    case: str
    target_path: str                     # 被测 feature/产物目录
    dimension_config: str = ""           # 指向 eval.dimensions.yaml
    prompts: list[PromptSpec] = Field(default_factory=list)


# --- rollup（D6③ skill@sha 一句话）---

class SkillRollup(BaseModel):
    """单作者首屏唯一结论："这版能不能发车"。优先级 FAIL > INSUFFICIENT > PASS。"""

    model_config = ConfigDict(extra="forbid")

    skill: str
    git_sha: str
    # 带档：PASS@full / PASS@probe / PASS@quick / FAIL（D10 C-08）
    best_mode: RunMode | None = None      # 此次 rollup 达到的最高档
    verdict: Verdict
    case_verdicts: dict[str, Verdict] = Field(default_factory=dict)
    note: str = ""


def rollup_skill(cases: list[tuple[str, Verdict]]) -> Verdict:
    """D6③ 优先级 FAIL > INSUFFICIENT_EVIDENCE > PASS。

    有红报红，不让系统态盖红灯。cases=[(case_name, verdict), ...]。
    """
    vs = [v for _, v in cases]
    if "FAIL" in vs:
        return "FAIL"
    if "INSUFFICIENT_EVIDENCE" in vs:
        return "INSUFFICIENT_EVIDENCE"
    return "PASS"


def insuff_if_unreproducible(record: RunRecord) -> Verdict:
    """D9.2 原则2：run 缺 git_sha 或（probe/full 缺 cc_version）→ INSUFFICIENT_EVIDENCE。

    quick 模式 cc_version 可空；probe/full 必须有。
    """
    if not record.git_sha:
        return "INSUFFICIENT_EVIDENCE"
    if record.mode in ("probe", "full") and not record.cc_version:
        return "INSUFFICIENT_EVIDENCE"
    return record.verdict.verdict
