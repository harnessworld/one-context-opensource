from __future__ import annotations

import pytest
from pydantic import ValidationError

from one_context.eval_lite.schema import (
    AssertionLink,
    Baseline,
    BaselineExpectation,
    CaseScenario,
    CaseVerdict,
    DimensionResult,
    PromptSpec,
    RunRecord,
    SkillRollup,
    insuff_if_unreproducible,
    rollup_skill,
)


# --- helpers ---

def _verdict(v: str) -> CaseVerdict:
    return CaseVerdict(verdict=v)


def _run_record(
    mode: str = "quick",
    git_sha: str = "a" * 40,
    cc_version: str = "",
    verdict: str = "PASS",
) -> RunRecord:
    return RunRecord(
        run_id="1000000-aaaa0000aaaa",
        skill="test-skill",
        case="test-case",
        mode=mode,
        git_sha=git_sha,
        cc_version=cc_version,
        verdict=_verdict(verdict),
    )


# --- rollup_skill ---

def test_rollup_skill_all_pass():
    assert rollup_skill([("a", "PASS"), ("b", "PASS")]) == "PASS"


def test_rollup_skill_fail_takes_priority():
    assert rollup_skill([("a", "PASS"), ("b", "FAIL")]) == "FAIL"


def test_rollup_skill_insuff_over_pass():
    assert rollup_skill([("a", "PASS"), ("b", "INSUFFICIENT_EVIDENCE")]) == "INSUFFICIENT_EVIDENCE"


def test_rollup_skill_fail_beats_insuff():
    assert rollup_skill([("a", "INSUFFICIENT_EVIDENCE"), ("b", "FAIL")]) == "FAIL"


def test_rollup_skill_empty():
    assert rollup_skill([]) == "PASS"


# --- insuff_if_unreproducible ---

def test_insuff_empty_git_sha():
    r = _run_record(git_sha="")
    assert insuff_if_unreproducible(r) == "INSUFFICIENT_EVIDENCE"


def test_insuff_probe_no_cc_version():
    r = _run_record(mode="probe", cc_version="")
    assert insuff_if_unreproducible(r) == "INSUFFICIENT_EVIDENCE"


def test_insuff_full_no_cc_version():
    r = _run_record(mode="full", cc_version="")
    assert insuff_if_unreproducible(r) == "INSUFFICIENT_EVIDENCE"


def test_insuff_quick_no_cc_ok():
    r = _run_record(mode="quick", cc_version="")
    # ponytail: git_sha present, quick doesn't need cc_version
    assert insuff_if_unreproducible(r) == "PASS"


def test_insuff_full_all_present():
    r = _run_record(mode="full", cc_version="1.0.0")
    assert insuff_if_unreproducible(r) == "PASS"


# --- extra="forbid" ---

def test_assertion_link_extra_forbid():
    with pytest.raises(ValidationError):
        AssertionLink(
            assertion_id="x", dimension="outcome", role="gate", unexpected_field=1,
        )


def test_dimension_result_extra_forbid():
    with pytest.raises(ValidationError):
        DimensionResult(dimension="outcome", role="gate", verdict="PASS", extra=1)


def test_run_record_extra_forbid():
    with pytest.raises(ValidationError):
        RunRecord(
            run_id="x", skill="s", case="c", mode="quick", git_sha="a",
            verdict=_verdict("PASS"), bogus=1,
        )


def test_baseline_extra_forbid():
    with pytest.raises(ValidationError):
        Baseline(skill="s", case="c", bogus=1)


# --- PromptSpec categories ---

def test_prompt_spec_valid_categories():
    for cat in ("explicit", "implicit", "context", "negative"):
        p = PromptSpec(category=cat, prompt=f"test {cat}")
        assert p.category == cat


def test_prompt_spec_invalid_category():
    with pytest.raises(ValidationError):
        PromptSpec(category="maybe", prompt="test")