from __future__ import annotations

import json

import pytest

from one_context.eval_lite.trace import (
    _skill_match,
    parse_skill_trigger,
)


# --- NDJSON helpers ---

def _assistant_tool_use(skill_name: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Using skill..."},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "Skill",
                    "input": {"skill": skill_name},
                },
            ],
        },
    }


def _result(cost: float = 0.01, duration: int = 500, turns: int = 1) -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "total_cost_usd": cost,
        "duration_ms": duration,
        "num_turns": turns,
    }


def _ndjson(*objs: dict) -> str:
    return "\n".join(json.dumps(o) for o in objs)


# --- _skill_match ---

def test_skill_match_exact():
    assert _skill_match("ponytail", "ponytail") is True


def test_skill_match_strips_namespace():
    assert _skill_match("ponytail:ponytail-help", "ponytail-help") is True


def test_skill_match_basename_equals_target():
    assert _skill_match("plugin:my-skill", "my-skill") is True


def test_skill_match_containment_fallback():
    # basename "remotion" is in target "remotion-pipelines"
    assert _skill_match("remotion", "remotion-pipelines") is True


def test_skill_match_reverse_containment():
    assert _skill_match("remotion-pipelines", "remotion") is True


def test_skill_match_empty_raw():
    assert _skill_match("", "anything") is False


def test_skill_match_no_match():
    assert _skill_match("banana", "orange") is False


# --- parse_skill_trigger ---

def test_parse_single_match():
    ndjson = _ndjson(_assistant_tool_use("my-skill"), _result())
    r = parse_skill_trigger(ndjson, "my-skill")
    assert r["triggered"] is True
    assert len(r["matched"]) == 1
    assert r["matched"][0] == "my-skill"
    assert r["cost_usd"] == 0.01
    assert r["duration_ms"] == 500
    assert r["num_turns"] == 1


def test_parse_no_tool_use():
    ndjson = _ndjson({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
    })
    r = parse_skill_trigger(ndjson, "my-skill")
    assert r["triggered"] is False
    assert r["matched"] == []
    assert r["all_calls"] == []


def test_parse_multiple_calls():
    ndjson = _ndjson(
        _assistant_tool_use("skill-a"),
        _assistant_tool_use("my-skill"),
        _assistant_tool_use("skill-b"),
    )
    r = parse_skill_trigger(ndjson, "my-skill")
    assert r["triggered"] is True
    assert len(r["matched"]) == 1
    assert len(r["all_calls"]) == 3


def test_parse_result_fields():
    ndjson = _ndjson(_result(cost=0.05, duration=1200, turns=3))
    r = parse_skill_trigger(ndjson, "anything")
    assert r["cost_usd"] == 0.05
    assert r["duration_ms"] == 1200
    assert r["num_turns"] == 3


def test_parse_no_result_line():
    ndjson = _ndjson(_assistant_tool_use("x"))
    r = parse_skill_trigger(ndjson, "x")
    assert r["cost_usd"] is None
    assert r["duration_ms"] is None
    assert r["num_turns"] is None


def test_parse_malformed_line_skipped():
    valid = json.dumps(_assistant_tool_use("x"))
    ndjson = f"not-json-at-all\n{valid}\n"
    r = parse_skill_trigger(ndjson, "x")
    assert r["triggered"] is True
    assert len(r["matched"]) == 1


def test_parse_empty_text():
    r = parse_skill_trigger("", "anything")
    assert r["triggered"] is False
    assert r["matched"] == []
    assert r["all_calls"] == []


def test_parse_namespace_match():
    ndjson = _ndjson(_assistant_tool_use("ponytail:ponytail-help"))
    r = parse_skill_trigger(ndjson, "ponytail-help")
    assert r["triggered"] is True


def test_parse_result_subtype():
    ndjson = _ndjson({"type": "result", "subtype": "error", "total_cost_usd": 0})
    r = parse_skill_trigger(ndjson, "x")
    assert r["result_subtype"] == "error"


# --- slow: real cc spawn ---

@pytest.mark.slow
def test_spawn_cc_probe_real(tmp_path):
    import os
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        pytest.skip("no ANTHROPIC key")
    from one_context.eval_lite.trace import spawn_cc_probe
    stdout, stderr = spawn_cc_probe("say hello", workdir=tmp_path, max_turns=1, timeout=60)
    assert stdout  # non-empty NDJSON


@pytest.mark.slow
def test_collect_cc_version_real():
    from one_context.eval_lite.trace import collect_cc_version
    v = collect_cc_version()
    assert v  # non-empty if claude CLI installed
