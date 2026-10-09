"""one-context · assertions engine (kept; reusable by eval_lite).

其余 eval 烟尘已随 skill-eval-runner 退役移除；本包仅保留零耦合的
`assertions.py`（9 类声明式断言 DSL + mini-jsonpath），供新框架客观维度复用。
"""

__all__ = ["assertions"]
