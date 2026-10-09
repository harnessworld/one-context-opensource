"""one-context · assertions engine (kept; reusable by eval_lite).

This package keeps the decoupled `assertions.py` (a declarative assertion
DSL with nine assertion kinds + mini-jsonpath) so the evaluation framework's
objective dimensions can reuse it.
"""

__all__ = ["assertions"]