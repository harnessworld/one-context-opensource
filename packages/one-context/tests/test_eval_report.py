from __future__ import annotations

import json
from pathlib import Path

import pytest

from one_context.eval_lite.report import (
    _aggregate_curve,
    _collect_runs,
    _rollup_verdict,
    render_html_report,
    render_report,
)


def _write_run_json(
    case_dir: Path,
    run_id: str,
    *,
    mode: str = "quick",
    git_sha: str = "a" * 40,
    verdict: str = "PASS",
    dims: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> None:
    runs_dir = case_dir / "__runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "run_id": run_id,
        "skill": "test-skill",
        "case": "test-case",
        "mode": mode,
        "git_sha": git_sha,
        "cc_version": "",
        "started_at": "",
        "duration_ms": 0,
        "verdict": {
            "verdict": verdict,
            "dimensions": dims or [],
            "rationale": "",
        },
        "warnings": warnings or [],
    }
    (runs_dir / f"{run_id}.json").write_text(json.dumps(rec), encoding="utf-8")


def _build_report_tree(
    tmp_path: Path,
    runs: list[dict] | None = None,
) -> Path:
    """Build skills/<skill>/evals/<case>/ with __runs/. Returns case_dir."""
    case_dir = tmp_path / "skills" / "test-skill" / "evals" / "test-case"
    for r in runs or []:
        _write_run_json(
            case_dir,
            r.get("run_id", "1000-abc"),
            mode=r.get("mode", "quick"),
            git_sha=r.get("git_sha", "a" * 40),
            verdict=r.get("verdict", "PASS"),
            dims=r.get("dims"),
            warnings=r.get("warnings"),
        )
    return case_dir


# --- _collect_runs ---

def test_collect_runs_sorted(tmp_path):
    cd = _build_report_tree(tmp_path, [
        {"run_id": "2000-bbb"},
        {"run_id": "1000-aaa"},
        {"run_id": "3000-ccc"},
    ])
    runs = _collect_runs(cd)
    assert [r["run_id"] for r in runs] == ["1000-aaa", "2000-bbb", "3000-ccc"]


def test_collect_runs_empty(tmp_path):
    cd = tmp_path / "skills" / "s" / "evals" / "c"
    cd.mkdir(parents=True)
    assert _collect_runs(cd) == []


def test_collect_runs_no_dir(tmp_path):
    assert _collect_runs(tmp_path / "nonexistent") == []


def test_collect_runs_malformed_json(tmp_path):
    cd = _build_report_tree(tmp_path, [{"run_id": "1000-ok"}])
    # add a corrupted file
    runs_dir = cd / "__runs"
    (runs_dir / "bad.json").write_text("not json", encoding="utf-8")
    runs = _collect_runs(cd)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "1000-ok"


# --- _rollup_verdict ---

def test_rollup_verdict_empty():
    v, m = _rollup_verdict([])
    assert v is None
    assert m is None


def test_rollup_verdict_latest():
    runs = [
        {"run_id": "1000", "mode": "quick", "verdict": {"verdict": "PASS"}},
        {"run_id": "2000", "mode": "full", "verdict": {"verdict": "FAIL"}},
    ]
    v, m = _rollup_verdict(runs)
    assert v == "FAIL"
    assert m == "full"


# --- _aggregate_curve ---

def test_aggregate_curve_basic():
    runs = [
        {"run_id": "1", "git_sha": "a" * 40, "mode": "quick",
         "verdict": {"verdict": "PASS"}},
    ]
    commits = [{"sha": "a" * 40, "short": "aaaaaaa", "msg": "init"}]
    curve = _aggregate_curve(runs, commits)
    assert len(curve) == 1
    assert curve[0]["verdict"] == "PASS"
    assert curve[0]["best_mode"] == "quick"


def test_aggregate_curve_no_runs_for_commit():
    curve = _aggregate_curve([], [{"sha": "b" * 40, "short": "bbbbbbb", "msg": "x"}])
    assert len(curve) == 1
    assert curve[0]["verdict"] is None


def test_aggregate_curve_picks_best_mode():
    runs = [
        {"run_id": "1", "git_sha": "a" * 40, "mode": "quick",
         "verdict": {"verdict": "PASS"}},
        {"run_id": "2", "git_sha": "a" * 40, "mode": "full",
         "verdict": {"verdict": "PASS"}},
    ]
    commits = [{"sha": "a" * 40, "short": "aaaaaaa", "msg": "init"}]
    curve = _aggregate_curve(runs, commits)
    assert curve[0]["best_mode"] == "full"
    assert curve[0]["has_full"] is True


# --- render_report ---

def test_render_report_no_runs(tmp_path):
    cd = _build_report_tree(tmp_path, [])
    skills_root = tmp_path / "skills"
    # case_dir doesn't exist yet; need parent
    cd.mkdir(parents=True, exist_ok=True)
    p = render_report("test-skill", "test-case", skills_root=skills_root)
    content = p.read_text(encoding="utf-8")
    assert "暂无 run" in content


def test_render_report_single_run(tmp_path):
    cd = _build_report_tree(tmp_path, [
        {"run_id": "1000-a", "verdict": "PASS",
         "dims": [{"dimension": "outcome", "role": "gate", "verdict": "PASS", "summary": "2/2"}]},
    ])
    skills_root = tmp_path / "skills"
    p = render_report("test-skill", "test-case", skills_root=skills_root)
    content = p.read_text(encoding="utf-8")
    assert "PASS" in content
    assert "outcome" in content


def test_render_report_warnings(tmp_path):
    cd = _build_report_tree(tmp_path, [
        {"run_id": "1000-a", "verdict": "PASS", "warnings": ["NO_NEGATIVE"]},
    ])
    skills_root = tmp_path / "skills"
    p = render_report("test-skill", "test-case", skills_root=skills_root)
    content = p.read_text(encoding="utf-8")
    assert "NO_NEGATIVE" in content


def test_render_report_case_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        render_report("no-skill", "no-case", skills_root=tmp_path / "skills")


# --- render_html_report ---

def test_render_html_creates_file(tmp_path):
    cd = _build_report_tree(tmp_path, [
        {"run_id": "1000-a", "verdict": "PASS"},
    ])
    skills_root = tmp_path / "skills"
    p = render_html_report("test-skill", "test-case", skills_root=skills_root)
    assert p.is_file()
    content = p.read_text(encoding="utf-8")
    assert "<table" in content
    assert "test-skill" in content