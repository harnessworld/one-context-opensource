"""eval_lite — systematic skill evaluation (thin shell, strict core).

Three run modes: quick (no agent spawn) / probe (verify skill triggering via
stream-json trace) / full (real agent run + LLM judge).
Three verdicts: PASS / FAIL / INSUFFICIENT_EVIDENCE.

A captured "satisfactory artifact" becomes the baseline; regressions are
measured against it. The core is protected by dimension aggregation, a
mandatory trigger dimension, and a rule-before-judge gate.
"""

__all__ = ["schema", "dimensions", "assertion_map"]