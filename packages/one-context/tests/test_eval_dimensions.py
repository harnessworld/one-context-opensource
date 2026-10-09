from __future__ import annotations

from pathlib import Path

import pytest

from one_context.eval_lite.dimensions import (
    BUILTIN_TEMPLATE,
    DimensionTemplateLoadError,
    default_template,
    load_dimensions,
)


def _dims_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "eval.dimensions.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _valid_dims_yaml() -> str:
    """All 7 dims with non-empty why_it_matters."""
    lines = []
    for dim in BUILTIN_TEMPLATE:
        lines.append(f'{dim}:\n  why_it_matters: "it matters for {dim}"')
    return "\n".join(lines)


# --- load_dimensions builtin fallback ---

def test_load_dimensions_missing_file_raises():
    """BUILTIN_TEMPLATE has empty why_it_matters → _validate raises D9.1."""
    with pytest.raises(DimensionTemplateLoadError, match="why_it_matters"):
        load_dimensions(Path("/nonexistent/dims.yaml"))


# --- load_dimensions from YAML ---

def test_load_dimensions_valid_yaml(tmp_path):
    p = _dims_yaml(tmp_path, _valid_dims_yaml())
    dims = load_dimensions(p)
    assert set(dims.keys()) == set(BUILTIN_TEMPLATE.keys())
    assert dims["outcome"]["why_it_matters"] == "it matters for outcome"


def test_load_dimensions_partial_merge(tmp_path):
    # Only override 3 dims; all 7 present in result
    content = (
        "outcome:\n  why_it_matters: 'artifact value'\n"
        "process:\n  why_it_matters: 'toolchain'\n"
        "style:\n  why_it_matters: 'style consistency'"
    )
    p = _dims_yaml(tmp_path, content)
    with pytest.raises(DimensionTemplateLoadError, match="why_it_matters"):
        load_dimensions(p)
    # Remaining dims still have empty why_it_matters → reject


def test_load_dimensions_all_filled(tmp_path):
    p = _dims_yaml(tmp_path, _valid_dims_yaml())
    dims = load_dimensions(p)
    assert dims["outcome"]["why_it_matters"] == "it matters for outcome"


# --- _validate ---

def test_validate_rejects_unknown_dimension(tmp_path):
    content = _valid_dims_yaml() + "\nunknown_dim:\n  why_it_matters: 'x'"
    p = _dims_yaml(tmp_path, content)
    with pytest.raises(DimensionTemplateLoadError, match="unknown dimension"):
        load_dimensions(p)


def test_validate_rejects_empty_why_it_matters(tmp_path):
    content = "outcome:\n  why_it_matters: ''"
    p = _dims_yaml(tmp_path, content)
    with pytest.raises(DimensionTemplateLoadError, match="why_it_matters"):
        load_dimensions(p)


def test_validate_rejects_whitespace_why_it_matters(tmp_path):
    content = "outcome:\n  why_it_matters: '   '"
    p = _dims_yaml(tmp_path, content)
    with pytest.raises(DimensionTemplateLoadError, match="why_it_matters"):
        load_dimensions(p)


# --- default_template ---

def test_default_template_is_deep_copy():
    t1 = default_template()
    t2 = default_template()
    t1["outcome"]["why_it_matters"] = "mutated"
    assert t2["outcome"]["why_it_matters"] == ""


def test_builtin_template_has_seven_dims():
    assert set(BUILTIN_TEMPLATE.keys()) == {
        "outcome", "process", "style", "efficiency",
        "surface", "trigger", "extension",
    }