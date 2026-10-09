"""assertion_map — assertion kind → dimension → gate/score default mapping.

This table is fixed up-front so assertions-engine output lines up with the
seven-dimension matrix schema. It is the default mapping;
skills/<name>/eval.dimensions.yaml can override it per assertion.
"""
from __future__ import annotations

from one_context.eval_lite.schema import AssertionLink, DimensionKey, EvidenceRole

# Full KIND_LITERAL set of eval/assertions.py.
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

# Default: which dimension each assertion kind belongs to + gate vs score.
# Design choices:
#   - file existence / schema contract kinds → outcome dimension, gate
#     (missing = red, short-circuit)
#   - text presence / regex counting / cross-file consistency → style or
#     outcome, gate
#   - tool_call_count → process dimension, score (bounded count, efficiency)
#   - count_compare → efficiency, score (delta tolerance for regression runs)
DEFAULT_DIM_WIN: dict[str, tuple[DimensionKey, EvidenceRole]] = {
    "file_exists":            ("outcome", "gate"),
    "file_absent":            ("outcome", "gate"),    # forbidden artifact present → gate
    "path_absent":            ("outcome", "gate"),
    "text_contains":          ("style",   "gate"),    # required keywords / fixed phrases → gate
    "text_absent":            ("style",   "gate"),    # forbidden words (e.g. signature drift) → gate
    "json_valid":             ("outcome", "gate"),    # artifact JSON structurally valid
    "json_field":             ("outcome", "gate"),    # required field / value range
    "count_compare":          ("efficiency", "score"),# count delta tolerance → regression scoring
    "regex_count":            ("style",   "score"),   # style counting (e.g. font-size hits)
    "tool_call_count":        ("process", "score"),   # toolchain step count vs declared → score
    "cross_file_consistency": ("outcome", "gate"),    # cross-file value alignment → gate
}


def default_link(kind: str, threshold: float | None = None) -> AssertionLink | None:
    """Default link for an assertion kind.

    Returns None for unregistered kinds (callers report an error; dangling
    mappings are not allowed).
    """
    d = DEFAULT_DIM_WIN.get(kind)
    if d is None:
        return None
    dim, role = d
    return AssertionLink(
        assertion_id="",        # the caller fills in the matching AssertionSpec.id
        dimension=dim,
        role=role,
        threshold=threshold,
    )


def links_for(assertion_specs: list[dict]) -> list[AssertionLink]:
    """Build links for a batch of AssertionSpec dicts.

    assertion_specs: [{id, kind, blocking?, threshold?}, ...]
    blocking=True assertions are forced to role=gate (gate evidence wins).
    """
    out: list[AssertionLink] = []
    for s in assertion_specs:
        kind = s.get("kind", "")
        base = default_link(kind, s.get("threshold"))
        if base is None:
            continue
        # An explicit blocking flag promotes the assertion to a gate even
        # when the kind default is score.
        if s.get("blocking"):
            base = base.model_copy(update={"role": "gate", "assertion_id": s.get("id", "")})
        else:
            base = base.model_copy(update={"assertion_id": s.get("id", "")})
        out.append(base)
    return out