"""dimensions — OpenAI 七维度通用模板（C-09 内置，capture 只填特化）。

设计 D9.1：每维度必填 why_it_matters（变差了你会真在意），空则该维度不生效。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from one_context.eval_lite.schema import DimensionKey, EvidenceRole

# 框架内置通用模板（七维 + 维护面 + 触发 + 扩展）。capture 时套用，只填特化断言。
# 每维度给 stage 提示（哪些档可测），但特化断言由 skill 自己补。
BUILTIN_TEMPLATE: dict[str, dict[str, Any]] = {
    "outcome": {
        "why_it_matters": "",   # 必填：产物没产出 / 契约破了 = 这版废了
        "role": "gate",
        "modes_ok": ["quick", "full"],
        # 特化断言：file_exists / json_field / cross_file_consistency 等，capture 填
    },
    "process": {
        "why_it_matters": "",   # 必填：绕路完成、工具链漂 = 下次换输入就崩
        "role": "score",
        "modes_ok": ["full"],   # 需要 trace，quick 不覆盖
        "threshold": 0.8,
    },
    "style": {
        "why_it_matters": "",   # 必填：风格不一致下游接不了手
        "role": "gate",         # 文件结构/必含词 gate；代码风格一致性可降 score
        "modes_ok": ["quick", "full"],
    },
    "efficiency": {
        "why_it_matters": "",   # 必填：thrashing/烧 token 大量触发被放大
        "role": "score",
        "modes_ok": ["quick", "full"],
        "threshold": 0.9,
        "tolerance": 0.2,       # 回归档容差 ±20%（D7 回归层）
    },
    "surface": {
        "why_it_matters": "",   # 必填：name/description 漂了触发边界腐烂看不见
        "role": "gate",         # diff 纳入报告，非数值
        "modes_ok": ["quick", "full"],
    },
    "trigger": {
        "why_it_matters": "",   # 必填：最易悄悄退化的地方
        "role": "gate",         # 负控误触 = FAIL
        "modes_ok": ["probe", "full"],  # quick 不覆盖（D8）
    },
    "extension": {
        "why_it_matters": "",   # 必填：任务完成不代表系统没被弄脏
        "role": "gate",
        "modes_ok": ["quick", "full"],  # 构建/清洁度本地可测
    },
}


class DimensionTemplateLoadError(ValueError):
    """dimensions.yaml 不合规（D9.1 空 why_it_matters / 未知维度）。"""


def load_dimensions(path: Path) -> dict[DimensionKey, dict[str, Any]]:
    """加载 skill 维度模板；缺省回退 BUILTIN_TEMPLATE。

    D9.1 强制：每维度 why_it_matters 非空，否则拒绝加载（不生效维度 = 无效）。
    """
    if not path.is_file():
        # 无 skill 模板 → 用内置（capture 时会强制提示补 why_it_matters）
        return _validate(dict(BUILTIN_TEMPLATE))  # type: ignore[arg-type]

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    merged: dict[str, dict[str, Any]] = {}
    for dim, spec in {**BUILTIN_TEMPLATE, **(raw or {})}.items():
        if isinstance(spec, dict):
            merged[dim] = spec
    return _validate(merged)


def _validate(merged: dict[str, dict[str, Any]]) -> dict[DimensionKey, dict[str, Any]]:
    valid_dims = set(BUILTIN_TEMPLATE.keys())
    out: dict[str, dict[str, Any]] = {}
    for dim, spec in merged.items():
        if dim not in valid_dims:
            raise DimensionTemplateLoadError(f"未知维度：{dim}")
        why = (spec.get("why_it_matters") or "").strip()
        if not why:
            raise DimensionTemplateLoadError(
                f"维度 {dim} 缺 why_it_matters（D9.1：变差了你会真在意，必须填，否则不生效）"
            )
        out[dim] = spec
    return out  # type: ignore[return-value]


def default_template() -> dict[str, dict[str, Any]]:
    """copy 用：写首版 eval.dimensions.yaml 时拿这个骨架。"""
    return {k: dict(v) for k, v in BUILTIN_TEMPLATE.items()}
