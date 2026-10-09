"""dimensions — built-in seven-dimension template.

Every dimension MUST define a non-empty ``why_it_matters`` ("what would you
genuinely care about if this got worse"); a dimension without it is rejected
at load time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from one_context.eval_lite.schema import DimensionKey, EvidenceRole

# Built-in generic template (seven dimensions). Capture applies this template
# and fills in the skill-specific assertions. Each dimension hints which run
# modes can measure it; the specialized assertions come from the skill.
BUILTIN_TEMPLATE: dict[str, dict[str, Any]] = {
    "outcome": {
        "why_it_matters": "",   # required: artifacts missing / contract broken = this version is useless
        "role": "gate",
        "modes_ok": ["quick", "full"],
        # specialized assertions: file_exists / json_field / cross_file_consistency etc., filled at capture
    },
    "process": {
        "why_it_matters": "",   # required: detours and toolchain drift = breaks on the next input
        "role": "score",
        "modes_ok": ["full"],   # needs a trace; quick does not cover it
        "threshold": 0.8,
    },
    "style": {
        "why_it_matters": "",   # required: inconsistent style cannot be handed downstream
        "role": "gate",         # file structure / required words gate; code-style consistency may score
        "modes_ok": ["quick", "full"],
    },
    "efficiency": {
        "why_it_matters": "",   # required: thrashing / token burn amplifies quickly
        "role": "score",
        "modes_ok": ["quick", "full"],
        "threshold": 0.9,
        "tolerance": 0.2,       # ±20% tolerance for regression scoring
    },
    "surface": {
        "why_it_matters": "",   # required: name/description drift silently rots the trigger boundary
        "role": "gate",         # diff is reported, not scored numerically
        "modes_ok": ["quick", "full"],
    },
    "trigger": {
        "why_it_matters": "",   # required: the most quietly degrading surface
        "role": "gate",         # a negative-control misfire = FAIL
        "modes_ok": ["probe", "full"],  # quick does not cover triggering
    },
    "extension": {
        "why_it_matters": "",   # required: task completion != system left clean
        "role": "gate",
        "modes_ok": ["quick", "full"],  # build / cleanliness are locally testable
    },
}


class DimensionTemplateLoadError(ValueError):
    """dimensions.yaml is invalid (empty why_it_matters / unknown dimension)."""


def load_dimensions(path: Path) -> dict[DimensionKey, dict[str, Any]]:
    """Load a skill's dimension template, falling back to BUILTIN_TEMPLATE.

    Enforcement: every dimension needs a non-empty ``why_it_matters``,
    otherwise the template is rejected (a dimension that cannot explain its
    own stakes is useless).
    """
    if not path.is_file():
        # No skill template → use the built-in one (capture prompts for why_it_matters).
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
            raise DimensionTemplateLoadError(f"unknown dimension: {dim}")
        why = (spec.get("why_it_matters") or "").strip()
        if not why:
            raise DimensionTemplateLoadError(
                f"dimension {dim} is missing why_it_matters "
                "(it must state what you would genuinely care about if this dimension degraded)"
            )
        out[dim] = spec
    return out  # type: ignore[return-value]


def default_template() -> dict[str, dict[str, Any]]:
    """Copy-paste skeleton for writing a first eval.dimensions.yaml."""
    return {k: dict(v) for k, v in BUILTIN_TEMPLATE.items()}