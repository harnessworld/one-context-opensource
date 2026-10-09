from __future__ import annotations

import json
from pathlib import Path

import pytest

import yaml

from one_context.eval_lite.capture import (
    CaptureError,
    CaptureReport,
    _expected_value,
    _flatten_assertions,
    _repo_root,
    capture_case,
)
from one_context.eval.assertions import AssertionSpec


# --- helpers ---

_MIN_DIMS_YAML = """\
outcome:
  why_it_matters: "产物完整"
  role: gate
  modes_ok: [quick, full]
  assertions:
    - kind: file_exists
      path: "production/content/01-script.md"
    - kind: file_exists
      path: "production/content/00-structure.md"
process:
  why_it_matters: "工具链步骤"
  role: score
  modes_ok: [full]
  threshold: 0.8
style:
  why_it_matters: "风格一致"
  role: gate
  modes_ok: [quick, full]
  assertions:
    - kind: file_exists
      path: "production/timing/flip-checklist.md"
      blocking: false
efficiency:
  why_it_matters: "效率达标"
  role: score
  modes_ok: [quick, full]
  threshold: 0.9
  tolerance: 0.2
surface:
  why_it_matters: "维护面稳定"
  role: gate
  modes_ok: [quick, full]
trigger:
  why_it_matters: "触发正确"
  role: gate
  modes_ok: [probe, full]
extension:
  why_it_matters: "扩展检查"
  role: gate
  modes_ok: [quick, full]
"""


def _build_one_context_tree(
    tmp_path: Path,
    skill: str = "test-skill",
    feature_files: dict[str, str] | None = None,
    dims_content: str = _MIN_DIMS_YAML,
) -> tuple[Path, Path, Path]:
    """Build minimal one-context tree in tmp_path. Returns (root, feature_dir, dims_path)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "meta").mkdir()
    (root / "meta" / "repos.yaml").write_text("repos: []\n", encoding="utf-8")
    # skill dir + dims
    skill_dir = root / "skills" / skill
    skill_dir.mkdir(parents=True)
    dims_path = skill_dir / "eval.dimensions.yaml"
    dims_path.write_text(dims_content, encoding="utf-8")
    # feature dir with artifacts
    feature_dir = root / "features" / "content-pipeline" / "test-feature"
    if feature_files:
        for rel, content in feature_files.items():
            p = feature_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    return root, feature_dir, dims_path


# --- _repo_root ---

def test_repo_root_finds_meta(tmp_path):
    root = tmp_path / "deep" / "repo"
    root.mkdir(parents=True)
    (root / "meta").mkdir()
    (root / "meta" / "repos.yaml").write_text("repos: []\n", encoding="utf-8")
    found = _repo_root(root / "sub" / "dir")
    # _repo_root walks up from start; if no meta/repos.yaml above, returns start
    # Create what we need
    sub = root / "sub" / "dir"
    sub.mkdir(parents=True)
    assert _repo_root(sub) == root


def test_repo_root_fallback(tmp_path):
    d = tmp_path / "nowhere"
    d.mkdir()
    assert _repo_root(d) == d.resolve()


# --- _expected_value ---

def test_expected_value_file_exists():
    spec = AssertionSpec(id="x", kind="file_exists", path="a.txt")
    assert _expected_value(spec, {}) is True


def test_expected_value_file_absent():
    spec = AssertionSpec(id="x", kind="file_absent", path="a.txt")
    assert _expected_value(spec, {}) is True


def test_expected_value_text_contains():
    spec = AssertionSpec(id="x", kind="text_contains", path="a.txt", needle="hello")
    assert _expected_value(spec, {}) == "hello"


def test_expected_value_json_field_with_actual():
    spec = AssertionSpec(id="x", kind="json_field", path="a.json", field="$.field")
    assert _expected_value(spec, {"actual": 42}) == 42


def test_expected_value_json_field_fallback():
    spec = AssertionSpec(id="x", kind="json_field", path="a.json", field="$.f", value="default")
    assert _expected_value(spec, {}) == "default"


def test_expected_value_unknown_kind():
    spec = AssertionSpec(id="x", kind="count_compare", path=".")
    assert _expected_value(spec, {}) is True


# --- _flatten_assertions ---

def test_flatten_assertions():
    dims = {
        "outcome": {
            "why_it_matters": "x",
            "assertions": [
                {"kind": "file_exists", "path": "a.txt"},
                {"kind": "file_exists", "path": "b.txt", "blocking": False},
            ],
        },
    }
    flat = _flatten_assertions(dims)
    assert len(flat) == 2
    assert flat[0][0] == "outcome"
    assert flat[0][1].kind == "file_exists"
    assert flat[0][1].blocking is True  # default
    assert flat[1][1].blocking is False


# --- capture_case ---

def test_capture_creates_baseline(tmp_path):
    feature_files = {
        "production/content/01-script.md": "# script",
        "production/content/00-structure.md": "# structure",
        "production/timing/flip-checklist.md": "# flip",
    }
    root, feat_dir, _ = _build_one_context_tree(tmp_path, feature_files=feature_files)
    r = capture_case(skill="test-skill", from_dir=feat_dir, prompt="do the thing")
    assert isinstance(r, CaptureReport)
    assert r.passed == 3
    assert r.failed == 0
    assert (r.case_dir / "baseline.json").is_file()
    assert (r.case_dir / "scenario.yaml").is_file()
    assert (r.case_dir / "prompts" / "explicit" / "01.md").is_file()


def test_capture_failed_gaps(tmp_path):
    # Only provide 1 of 3 expected files
    feature_files = {
        "production/content/01-script.md": "# script",
    }
    root, feat_dir, _ = _build_one_context_tree(tmp_path, feature_files=feature_files)
    r = capture_case(skill="test-skill", from_dir=feat_dir, prompt="do the thing")
    assert r.passed == 1
    assert r.failed == 2
    assert len(r.gaps) == 2


def test_capture_from_dir_not_exist(tmp_path):
    with pytest.raises(CaptureError, match="不存在"):
        capture_case(skill="x", from_dir=Path("/nonexistent_dir_xyz"), prompt="test")


def test_capture_empty_prompt(tmp_path):
    root, feat_dir, _ = _build_one_context_tree(tmp_path,
        feature_files={"production/content/01-script.md": "# s"})
    with pytest.raises(CaptureError, match="不能为空"):
        capture_case(skill="test-skill", from_dir=feat_dir, prompt="   ")


def test_capture_scenario_yaml_roundtrip(tmp_path):
    feature_files = {"production/content/01-script.md": "# s"}
    root, feat_dir, _ = _build_one_context_tree(tmp_path, feature_files=feature_files)
    r = capture_case(skill="test-skill", from_dir=feat_dir, prompt="run it")
    from one_context.eval_lite.schema import CaseScenario
    scenario = CaseScenario.model_validate(
        yaml.safe_load((r.scenario_path).read_text(encoding="utf-8"))
    )
    assert scenario.skill == "test-skill"
    assert scenario.prompts[0].category == "explicit"
    assert scenario.prompts[0].prompt == "run it"


def test_capture_baseline_json_roundtrip(tmp_path):
    feature_files = {"production/content/01-script.md": "# s"}
    root, feat_dir, _ = _build_one_context_tree(tmp_path, feature_files=feature_files)
    r = capture_case(skill="test-skill", from_dir=feat_dir, prompt="run it")
    from one_context.eval_lite.schema import Baseline
    bl = Baseline.model_validate_json((r.baseline_path).read_text(encoding="utf-8"))
    assert bl.skill == "test-skill"
    assert len(bl.expectations) >= 1


def test_capture_needs_negatives(tmp_path):
    feature_files = {"production/content/01-script.md": "# s"}
    root, feat_dir, _ = _build_one_context_tree(tmp_path, feature_files=feature_files)
    r = capture_case(skill="test-skill", from_dir=feat_dir, prompt="run it")
    assert r.needs_negatives is True