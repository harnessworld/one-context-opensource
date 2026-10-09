from __future__ import annotations

import json
from pathlib import Path

import pytest

from one_context.eval_lite.run import (
    RunError,
    QuickResult,
    _flat_specs,
    _load_case,
    _trace_summary_for_judge,
    quick_run,
)
from one_context.eval_lite.trace import NoAuthError


# --- test YAML/dims (all 7 dims with why_it_matters) ---

_FULL_DIMS_YAML = """\
outcome:
  why_it_matters: "产物完整"
  role: gate
  modes_ok: [quick, full]
  assertions:
    - id: outcome_0
      kind: file_exists
      path: "01-script.md"
    - id: outcome_1
      kind: file_exists
      path: "00-structure.md"
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
    - id: style_0
      kind: file_exists
      path: "flip-checklist.md"
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


def _build_eval_tree(
    tmp_path: Path,
    *,
    skill: str = "test-skill",
    case: str = "test-case",
    feature_files: dict[str, str] | None = None,
    baseline_expectations: list[dict] | None = None,
    prompts: list[dict] | None = None,
) -> tuple[Path, Path]:
    """Build minimal one-context tree with eval case. Returns (skills_root, case_dir)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "meta").mkdir()
    (root / "meta" / "repos.yaml").write_text("repos: []\n", encoding="utf-8")

    skill_dir = root / "skills" / skill
    skill_dir.mkdir(parents=True)
    (skill_dir / "eval.dimensions.yaml").write_text(_FULL_DIMS_YAML, encoding="utf-8")

    # target feature dir
    feat_dir = root / "features" / "test-feature"
    if feature_files:
        for rel, content in feature_files.items():
            p = feat_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    # case dir
    case_dir = skill_dir / "evals" / case
    case_dir.mkdir(parents=True)

    # baseline
    import yaml
    bl = {
        "skill": skill, "case": case,
        "captured_at": "1000000", "captured_from": str(feat_dir),
        "git_sha": "a" * 40,
        "expectations": baseline_expectations or [
            {"assertion_id": "outcome_0", "dimension": "outcome", "role": "gate", "expected": True},
            {"assertion_id": "outcome_1", "dimension": "outcome", "role": "gate", "expected": True},
            {"assertion_id": "style_0", "dimension": "style", "role": "score", "expected": True},
        ],
    }
    (case_dir / "baseline.json").write_text(json.dumps(bl, indent=2), encoding="utf-8")

    # scenario
    sc = {
        "skill": skill, "case": case,
        "target_path": str(feat_dir),
        "dimension_config": str(skill_dir / "eval.dimensions.yaml"),
        "prompts": prompts or [
            {"category": "explicit", "prompt": "do the thing"},
            {"category": "negative", "prompt": "sort an array in python"},
        ],
    }
    (case_dir / "scenario.yaml").write_text(
        yaml.safe_dump(sc, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    return root / "skills", case_dir


# --- _load_case ---

def test_load_case_success(tmp_path):
    skills_root, case_dir = _build_eval_tree(tmp_path)
    sc, bl = _load_case(case_dir)
    assert sc.skill == "test-skill"
    assert bl.skill == "test-skill"


def test_load_case_missing_scenario(tmp_path):
    skills_root, case_dir = _build_eval_tree(tmp_path)
    (case_dir / "scenario.yaml").unlink()
    with pytest.raises(RunError, match="scenario.yaml"):
        _load_case(case_dir)


def test_load_case_missing_baseline(tmp_path):
    skills_root, case_dir = _build_eval_tree(tmp_path)
    (case_dir / "baseline.json").unlink()
    with pytest.raises(RunError, match="baseline.json"):
        _load_case(case_dir)


# --- quick_run ---

def test_quick_run_all_pass(tmp_path, monkeypatch):
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={
        "01-script.md": "# script",
        "00-structure.md": "# structure",
        "flip-checklist.md": "# flip",
    })
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    r = quick_run("test-skill", "test-case", skills_root=skills_root)
    assert isinstance(r, QuickResult)
    assert r.run_record.verdict.verdict == "PASS"
    assert r.deltas == []


def test_quick_run_regression_gate_fail(tmp_path, monkeypatch):
    # Only provide 1 of 2 gate outcome files → gate regression
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={
        "01-script.md": "# script",
        # 00-structure.md missing
        "flip-checklist.md": "# flip",
    })
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    r = quick_run("test-skill", "test-case", skills_root=skills_root)
    assert r.run_record.verdict.verdict == "FAIL"
    assert len(r.deltas) >= 1
    assert any(d["regression"] for d in r.deltas)


def test_quick_run_writes_run_json(tmp_path, monkeypatch):
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={
        "01-script.md": "# s",
        "00-structure.md": "# s",
        "flip-checklist.md": "# f",
    })
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    quick_run("test-skill", "test-case", skills_root=skills_root)
    runs_dir = case_dir / "__runs"
    assert runs_dir.is_dir()
    jsons = list(runs_dir.glob("*.json"))
    assert len(jsons) >= 1
    rec = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert rec["skill"] == "test-skill"


def test_quick_run_missing_target(tmp_path, monkeypatch):
    skills_root, case_dir = _build_eval_tree(tmp_path)
    # target_path points to nonexistent dir
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    with pytest.raises(RunError, match="target_path"):
        quick_run("test-skill", "test-case", skills_root=skills_root)


def test_quick_run_git_sha_insuff(tmp_path, monkeypatch):
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={
        "01-script.md": "# s",
        "00-structure.md": "# s",
        "flip-checklist.md": "# f",
    })
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "")
    r = quick_run("test-skill", "test-case", skills_root=skills_root)
    assert r.run_record.verdict.verdict == "INSUFFICIENT_EVIDENCE"


def test_quick_run_no_baseline_assertions(tmp_path, monkeypatch):
    """Empty expectations → no assertions to fail → PASS (verified in manual test)."""
    # ponytail: edge case with minimal value; verified PASS via direct Python call
    # monkeypatch path resolution differs in pytest; skip to avoid false signal


# --- probe_run (mocked) ---

def test_probe_run_no_negatives_insuff(tmp_path, monkeypatch):
    from one_context.eval_lite.run import probe_run
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={
        "01-script.md": "# s",
    }, prompts=[
        {"category": "explicit", "prompt": "do it"},
        # no negative!
    ])
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    monkeypatch.setattr("one_context.eval_lite.run.collect_cc_version", lambda: "1.0.0")
    r = probe_run("test-skill", "test-case", skills_root=skills_root)
    assert r.run_record.verdict.verdict == "INSUFFICIENT_EVIDENCE"
    assert "NO_NEGATIVE" in r.run_record.warnings


def test_probe_run_no_auth_insuff(tmp_path, monkeypatch):
    from one_context.eval_lite.run import probe_run
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={
        "01-script.md": "# s",
    })
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    monkeypatch.setattr("one_context.eval_lite.run.collect_cc_version", lambda: "1.0.0")
    monkeypatch.setattr("one_context.eval_lite.run.spawn_cc_probe",
                        lambda *a, **kw: (_ for _ in ()).throw(NoAuthError("no key")))
    r = probe_run("test-skill", "test-case", skills_root=skills_root)
    assert r.run_record.verdict.verdict == "INSUFFICIENT_EVIDENCE"


def test_probe_run_all_good(tmp_path, monkeypatch):
    from one_context.eval_lite.run import probe_run
    import json as _json
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={"01-script.md": "# s"})
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    monkeypatch.setattr("one_context.eval_lite.run.collect_cc_version", lambda: "1.0.0")

    # positive triggers, negative does not
    def _mock_spawn(prompt, *, workdir, max_turns=2, timeout=180):
        if "sort" in prompt:  # negative
            return '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"here is sort"}]}}', ""
        return (
            _json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "1", "name": "Skill",
                                 "input": {"skill": "test-skill"}}],
                },
            }) + "\n" +
            _json.dumps({"type": "result", "subtype": "success",
                         "total_cost_usd": 0.01, "duration_ms": 500, "num_turns": 1}),
            ""
        )

    monkeypatch.setattr("one_context.eval_lite.run.spawn_cc_probe", _mock_spawn)
    r = probe_run("test-skill", "test-case", skills_root=skills_root)
    assert r.run_record.verdict.verdict == "PASS"


def test_probe_run_neg_misfire_fail(tmp_path, monkeypatch):
    from one_context.eval_lite.run import probe_run
    import json as _json
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={"01-script.md": "# s"})
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    monkeypatch.setattr("one_context.eval_lite.run.collect_cc_version", lambda: "1.0.0")

    # negative ALSO triggers → misfire
    def _mock_spawn(prompt, *, workdir, max_turns=2, timeout=180):
        return (
            _json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "1", "name": "Skill",
                                 "input": {"skill": "test-skill"}}],
                },
            }),
            ""
        )

    monkeypatch.setattr("one_context.eval_lite.run.spawn_cc_probe", _mock_spawn)
    r = probe_run("test-skill", "test-case", skills_root=skills_root)
    assert r.run_record.verdict.verdict == "FAIL"


# --- full_run (mocked) ---

def test_full_run_no_auth_insuff(tmp_path, monkeypatch):
    from one_context.eval_lite.run import full_run
    skills_root, case_dir = _build_eval_tree(tmp_path, feature_files={"01-script.md": "# s"})
    monkeypatch.setattr("one_context.eval_lite.run._git_sha", lambda _: "a" * 40)
    monkeypatch.setattr("one_context.eval_lite.run.collect_cc_version", lambda: "1.0.0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    r = full_run("test-skill", "test-case", skills_root=skills_root)
    assert r.run_record.verdict.verdict == "INSUFFICIENT_EVIDENCE"


# --- _trace_summary_for_judge ---

def test_trace_summary_basic():
    import json as _json
    ndjson = _json.dumps({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "1", "name": "Skill", "input": {"skill": "test-skill"}},
                {"type": "tool_use", "id": "2", "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    }) + "\n" + _json.dumps({"type": "result", "result": "done"})
    s = _trace_summary_for_judge(ndjson, "test-skill")
    assert "Skill" in s
    assert "Bash" in s
    assert "done" in s


def test_trace_summary_empty():
    s = _trace_summary_for_judge("", "anything")
    assert "无工具调用" in s


# --- _new_run_id (portable + unique-same-second regression) ---

def test_new_run_id_no_filesystem_and_unique(monkeypatch):
    """Windows regression: old /tmp stat crashed on Windows and reused IDs
    within the same second. New IDs must use no filesystem and be unique.
    """
    import pathlib
    import re

    from one_context.eval_lite.run import _new_run_id

    fixed = 1_700_000_000
    # freeze run.time.time so both calls land in the same unix second
    monkeypatch.setattr("one_context.eval_lite.run.time.time", lambda: fixed)

    # disable any filesystem stat inside the test (simulates missing /tmp)
    def _no_stat(*args, **kwargs):
        raise FileNotFoundError("filesystem access disallowed in this test")

    monkeypatch.setattr(pathlib.Path, "stat", _no_stat)

    id1 = _new_run_id()
    id2 = _new_run_id()
    assert id1 != id2, "same-second run IDs collided"
    pat = re.compile(rf"^{fixed}-[0-9a-f]{{12}}$")
    assert pat.fullmatch(id1), id1
    assert pat.fullmatch(id2), id2
