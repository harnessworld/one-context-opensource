"""assertion_map — C-06 断言 kind → 输出字段 → 归属维度 → 门控/评分。

落地前必须定死，否则 assertions.py 输出与 7 维矩阵对不上 schema。
本表是默认映射；skills/<name>/eval.dimensions.yaml 可逐条覆盖。
"""
from __future__ import annotations

from one_context.eval_lite.schema import AssertionLink, DimensionKey, EvidenceRole

# assertions.py 的 KIND_LITERAL 全集（含 ISS-024/M5 扩展，见 assertions.py:60）
ALL_KINDS = (
    "file_exists",
    "file_absent",
    "path_absent",
    "text_contains",
    "text_absent",
    "json_valid",
    "json_field",
    "count_compare",
    "regex_count",
    "tool_call_count",
    "cross_file_consistency",
)

# 默认：每类 kind 归到哪个维度 + 门控/评分。
# 设计选择（D6①）：
#   - 文件存在/schema 契约类 → outcome 维度，gate（缺失即红，短路）
#   - 内容包含/正则计数/跨文件一致性 → style 或 outcome，gate
#   - tool_call_count → process 维度，score（数量在阈值内，效率相关）
#   - count_compare → efficiency/process，score（delta 容差）
DEFAULT_DIM_WIN: dict[str, tuple[DimensionKey, EvidenceRole]] = {
    "file_exists":            ("outcome", "gate"),
    "file_absent":            ("outcome", "gate"),    # 禁产物存在 → gate
    "path_absent":            ("outcome", "gate"),
    "text_contains":          ("style",   "gate"),    # 必含关键词/本频道固定句 → gate
    "text_absent":            ("style",   "gate"),    # 禁词（署名越界等） → gate
    "json_valid":             ("outcome", "gate"),    # 产物 JSON 结构合法
    "json_field":             ("outcome", "gate"),    # 必填字段/范围
    "count_compare":          ("efficiency", "score"),# 数量 delta 容差 → 回归档评分
    "regex_count":            ("style",   "score"),   # 风格计数（如字号命中数）
    "tool_call_count":        ("process", "score"),   # 工具链步骤数 vs 声明 → score
    "cross_file_consistency": ("outcome", "gate"),    # 五数对齐/URL 在 03 里 → gate
}


def default_link(kind: str, threshold: float | None = None) -> AssertionLink | None:
    """kind 未登记返回 None（调用方报 error，C-06 不允许悬空映射）。"""
    d = DEFAULT_DIM_WIN.get(kind)
    if d is None:
        return None
    dim, role = d
    return AssertionLink(
        assertion_id="",        # 由调用方填对应 AssertionSpec.id
        dimension=dim,
        role=role,
        threshold=threshold,
    )


def links_for(assertion_specs: list[dict]) -> list[AssertionLink]:
    """从 AssertionSpec（dict 形）批量建 link。

    assertion_specs: [{id, kind, blocking?, threshold?}, ...]
    blocking=True 的断言强制 role=gate（D6① 门控证据优先）。
    """
    out: list[AssertionLink] = []
    for s in assertion_specs:
        kind = s.get("kind", "")
        base = default_link(kind, s.get("threshold"))
        if base is None:
            continue
        # 阻塞标记显式抬为 gate（即便默认是 score 的，标记 blocking 则当门控）
        if s.get("blocking"):
            base = base.model_copy(update={"role": "gate", "assertion_id": s.get("id", "")})
        else:
            base = base.model_copy(update={"assertion_id": s.get("id", "")})
        out.append(base)
    return out
