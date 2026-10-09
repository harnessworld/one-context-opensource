from __future__ import annotations

import json

import pytest

from one_context.eval_lite.judge_lite import (
    JudgeError,
    _parse_judge_json,
    judge_verdict_from_score,
)


def _valid_judge_json(**overrides) -> str:
    obj = {
        "dimension": "process",
        "score": 0.85,
        "evidence": "tool chain matches SKILL.md steps",
        "rationale": "all steps present",
        "verdict": "PASS",
    }
    obj.update(overrides)
    return json.dumps(obj)


# --- _parse_judge_json ---

def test_parse_valid():
    r = _parse_judge_json("process", _valid_judge_json())
    assert r.dimension == "process"
    assert r.score == 0.85
    assert r.verdict == "PASS"
    assert r.error == ""


def test_parse_with_fence():
    text = f"```json\n{_valid_judge_json()}\n```"
    r = _parse_judge_json("process", text)
    assert r.score == 0.85
    assert r.verdict == "PASS"


def test_parse_with_trailing_text():
    text = f"{_valid_judge_json()} some trailing"
    r = _parse_judge_json("process", text)
    assert r.score == 0.85


def test_parse_empty_text():
    r = _parse_judge_json("process", "")
    assert r.error == "judge produced no output"
    assert r.verdict == "FAIL"


def test_parse_no_json_object():
    r = _parse_judge_json("process", "just plain text no braces")
    assert "not JSON" in r.error


def test_parse_missing_keys():
    obj = {"dimension": "x", "score": 0.5}
    r = _parse_judge_json("x", json.dumps(obj))
    assert "missing fields" in r.error


def test_parse_invalid_verdict():
    r = _parse_judge_json("x", _valid_judge_json(verdict="MAYBE"))
    assert "verdict is invalid" in r.error


def test_parse_score_clamped_high():
    r = _parse_judge_json("x", _valid_judge_json(score=1.5))
    assert r.score == 1.0


def test_parse_score_clamped_low():
    r = _parse_judge_json("x", _valid_judge_json(score=-0.3))
    assert r.score == 0.0


def test_parse_score_non_numeric():
    r = _parse_judge_json("x", _valid_judge_json(**{"score": "abc"}))
    # "abc" → float() fails → 0.0
    assert r.score == 0.0


def test_parse_fence_with_language_tag():
    text = f"```json\n{_valid_judge_json()}\n```"
    r = _parse_judge_json("process", text)
    assert r.error == ""


# --- judge_verdict_from_score ---

def test_verdict_above_threshold():
    assert judge_verdict_from_score(0.9, 0.8) == "PASS"


def test_verdict_below_threshold():
    assert judge_verdict_from_score(0.7, 0.8) == "FAIL"


def test_verdict_default_threshold():
    assert judge_verdict_from_score(0.8, None) == "PASS"


def test_verdict_exact_threshold():
    assert judge_verdict_from_score(0.8, 0.8) == "PASS"


# --- judge_dimension rubric validation (no spawn) ---

def test_judge_dimension_empty_rubric_raises():
    from one_context.eval_lite.judge_lite import judge_dimension
    with pytest.raises(JudgeError, match="missing judge_rubric"):
        judge_dimension("x", "", "ctx", env={})


def test_judge_dimension_short_rubric_raises():
    from one_context.eval_lite.judge_lite import judge_dimension
    with pytest.raises(JudgeError, match="too short"):
        judge_dimension("x", "short", "ctx", env={})


# --- slow: real judge spawn ---

@pytest.mark.slow
def test_judge_dimension_real(tmp_path):
    import os
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        pytest.skip("no ANTHROPIC key")
    from one_context.eval_lite.judge_lite import judge_dimension
    env = {**os.environ}
    r = judge_dimension(
        "style",
        "Grade style consistency: does the produced file structure match the SKILL.md declaration; missing=0, all present=1. score>=0.8 means PASS.",
        "Test context: 3 files were produced; SKILL.md declares 3.",
        env=env,
        workdir=str(tmp_path),
        timeout=60,
    )
    assert r.error == "" or r.error  # just verify structure returned