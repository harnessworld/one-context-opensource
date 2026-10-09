"""eval_lite — Skill 系统化评测（轻壳重核）。

feature: features/core/skill-systematic-eval
design: features/core/skill-systematic-eval/tech_design.md (D2)

三档 run: quick(无 cc) / probe(stream-json 验触发) / full(worktree+judge)。
verdict 三态: PASS / FAIL / INSUFFICIENT_EVIDENCE。
满意度即标准 baseline（无 sign-off）；重核靠 D6 聚合闭环 + D8 触发档 + D9 五原则 gate。
"""

__all__ = ["schema", "dimensions", "assertion_map"]
