"""Unit tests for the declarative assertions DSL (Phase 2.6.B)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from one_context.eval.assertions import (
    AssertionContext,
    AssertionSpec,
    _resolve_jsonpath,
    _tokenize_jsonpath,
    merge_assertions,
    run_assertions,
    summarize,
)


# ---------------------------------------------------------------------------
# mini-jsonpath
# ---------------------------------------------------------------------------


def test_jsonpath_root_only():
    assert _tokenize_jsonpath("$") == []
    assert _resolve_jsonpath({"a": 1}, "$") == [{"a": 1}]


def test_jsonpath_nested_keys():
    doc = {"a": {"b": {"c": 7}}}
    assert _resolve_jsonpath(doc, "$.a.b.c") == [7]
    assert _resolve_jsonpath(doc, "$.a.b") == [{"c": 7}]


def test_jsonpath_wildcard_array():
    doc = {"items": [{"x": 1}, {"x": 2}, {"x": 3}]}
    assert _resolve_jsonpath(doc, "$.items[*].x") == [1, 2, 3]


def test_jsonpath_wildcard_dict_values():
    doc = {"by_id": {"a": 1, "b": 2}}
    leaves = _resolve_jsonpath(doc, "$.by_id[*]")
    assert sorted(leaves) == [1, 2]


def test_jsonpath_index():
    doc = {"items": ["a", "b", "c"]}
    assert _resolve_jsonpath(doc, "$.items[0]") == ["a"]
    assert _resolve_jsonpath(doc, "$.items[2]") == ["c"]
    assert _resolve_jsonpath(doc, "$.items[5]") == []  # out of bounds → empty


def test_jsonpath_missing_key_yields_empty():
    doc = {"a": {"b": 1}}
    assert _resolve_jsonpath(doc, "$.a.missing") == []
    assert _resolve_jsonpath(doc, "$.x.y.z") == []


def test_jsonpath_chained_wildcards():
    doc = {"groups": [{"items": [1, 2]}, {"items": [3, 4]}]}
    assert _resolve_jsonpath(doc, "$.groups[*].items[*]") == [1, 2, 3, 4]


def test_jsonpath_invalid_raises():
    with pytest.raises(ValueError):
        _tokenize_jsonpath("foo")  # no $
    with pytest.raises(ValueError):
        _tokenize_jsonpath("$..a")  # empty key
    with pytest.raises(ValueError):
        _tokenize_jsonpath("$[abc]")  # non-int, non-*
    with pytest.raises(ValueError):
        _tokenize_jsonpath("$[1")  # unclosed bracket


# ---------------------------------------------------------------------------
# helpers: build an AssertionContext over a tmp tree
# ---------------------------------------------------------------------------


def _ctx(
    tmp_path: Path,
    *,
    target_path: str = "production/info-radar",
    final_text: str = "",
    stdout: str = "",
    stderr_tail: str = "",
    produced: list[str] | None = None,
) -> AssertionContext:
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    base = sandbox_root / target_path
    base.mkdir(parents=True, exist_ok=True)
    return AssertionContext(
        sandbox_root=sandbox_root,
        artifacts_base=base,
        final_text=final_text,
        stdout=stdout,
        stderr_tail=stderr_tail,
        tool_calls=[],
        produced_paths=set(produced or []),
    )


def _spec(**kw) -> AssertionSpec:
    kw.setdefault("id", kw.get("kind", "x") + "-id")
    return AssertionSpec(**kw)


# ---------------------------------------------------------------------------
# file_exists / file_absent
# ---------------------------------------------------------------------------


def test_file_exists_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "01.json").write_text("{}", encoding="utf-8")
    [r] = run_assertions([_spec(kind="file_exists", path="01.json")], ctx)
    assert r.status == "pass"
    assert r.detail["size"] == 2


def test_file_exists_fail(tmp_path):
    ctx = _ctx(tmp_path)
    [r] = run_assertions([_spec(kind="file_exists", path="missing.json")], ctx)
    assert r.status == "fail"


def test_file_absent_pass(tmp_path):
    ctx = _ctx(tmp_path)
    [r] = run_assertions([_spec(kind="file_absent", path="should-not-exist")], ctx)
    assert r.status == "pass"


def test_file_absent_fail(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "extra.md").write_text("hi", encoding="utf-8")
    [r] = run_assertions([_spec(kind="file_absent", path="extra.md")], ctx)
    assert r.status == "fail"


# ---------------------------------------------------------------------------
# path_absent
# ---------------------------------------------------------------------------


def test_path_absent_pass(tmp_path):
    ctx = _ctx(tmp_path)
    [r] = run_assertions(
        [_spec(kind="path_absent", glob="features/content-pipeline/*")], ctx,
    )
    assert r.status == "pass"
    assert r.detail["match_count"] == 0


def test_path_absent_fail(tmp_path):
    ctx = _ctx(tmp_path)
    bad = ctx.sandbox_root / "features" / "content-pipeline" / "new-feat" / "spec.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("oops", encoding="utf-8")
    [r] = run_assertions(
        [_spec(kind="path_absent", glob="features/content-pipeline/*")], ctx,
    )
    assert r.status == "fail"
    assert r.detail["match_count"] == 1
    assert "features/content-pipeline/new-feat/spec.md" in r.detail["matches"]


# ---------------------------------------------------------------------------
# text_contains / text_absent
# ---------------------------------------------------------------------------


def test_text_contains_substring_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "04.md").write_text(
        "...\nStep 6 在评测模式下跳过\n", encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="text_contains", source="file", path="04.md",
        needle="Step 6 在评测模式下跳过",
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 1


def test_text_contains_count_min_fail(tmp_path):
    ctx = _ctx(tmp_path, final_text="hello world")
    [r] = run_assertions([_spec(
        kind="text_contains", source="final_text", needle="banana", count_min=1,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 0


def test_text_contains_regex(tmp_path):
    ctx = _ctx(tmp_path, final_text="FPS = 30 in two places: FPS=30 again")
    [r] = run_assertions([_spec(
        kind="text_contains", source="final_text",
        pattern=r"FPS\s*=\s*30", count_min=2,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 2


def test_text_absent_pass(tmp_path):
    ctx = _ctx(tmp_path, final_text="all good")
    [r] = run_assertions([_spec(
        kind="text_absent", source="final_text", needle="--mode split",
    )], ctx)
    assert r.status == "pass"


def test_text_absent_fail(tmp_path):
    ctx = _ctx(tmp_path, final_text="ran with --mode split")
    [r] = run_assertions([_spec(
        kind="text_absent", source="final_text", needle="--mode split",
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 1


# ---------------------------------------------------------------------------
# json_valid + json_field (all 6 ops)
# ---------------------------------------------------------------------------


def test_json_valid_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "01.json").write_text(
        '[{"title": "x", "url": "https://a"}]', encoding="utf-8",
    )
    [r] = run_assertions([_spec(kind="json_valid", path="01.json")], ctx)
    assert r.status == "pass"


def test_json_valid_error_on_bad_json(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "broken.json").write_text(
        "{ not valid", encoding="utf-8",
    )
    [r] = run_assertions([_spec(kind="json_valid", path="broken.json")], ctx)
    assert r.status == "error"
    assert "JSONDecodeError" in r.detail["error_class"]


def test_json_field_exists(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "doc.json").write_text(
        '{"items": [{"x": 1}]}', encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="json_field", path="doc.json", field="$.items[*].x", op="exists",
    )], ctx)
    assert r.status == "pass"


def test_json_field_eq(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "doc.json").write_text('{"FPS": 30}', encoding="utf-8")
    [r] = run_assertions([_spec(
        kind="json_field", path="doc.json", field="$.FPS", op="eq", value=30,
    )], ctx)
    assert r.status == "pass"


def test_json_field_in_range_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "03.json").write_text(
        json.dumps([{"total_score": 42}, {"total_score": 87}]),
        encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="json_field", path="03.json", field="$[*].total_score",
        op="in_range", min=0, max=100,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["checked"] == 2


def test_json_field_in_range_fail(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "03.json").write_text(
        json.dumps([{"total_score": 42}, {"total_score": 150}]),
        encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="json_field", path="03.json", field="$[*].total_score",
        op="in_range", min=0, max=100,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["out_of_range_count"] == 1


def test_json_field_type_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "doc.json").write_text(
        '[{"title": "a"}, {"title": "b"}]', encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="json_field", path="doc.json", field="$[*].title",
        op="type", expected_type="string",
    )], ctx)
    assert r.status == "pass"


def test_json_field_unique_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "02.json").write_text(
        json.dumps([{"url": "a"}, {"url": "b"}, {"url": "c"}]), encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="json_field", path="02.json", field="$[*].url", op="unique",
    )], ctx)
    assert r.status == "pass"


def test_json_field_unique_fail(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "02.json").write_text(
        json.dumps([{"url": "a"}, {"url": "a"}, {"url": "b"}]), encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="json_field", path="02.json", field="$[*].url", op="unique",
    )], ctx)
    assert r.status == "fail"
    assert r.detail["duplicates"] == 1


def test_json_field_all_match_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "01.json").write_text(
        json.dumps([
            {"source": "hacker_news"}, {"source": "hacker_news"},
        ]), encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="json_field", path="01.json", field="$[*].source",
        op="all_match", value=r"^hacker_news$",
    )], ctx)
    assert r.status == "pass"


# ---------------------------------------------------------------------------
# count_compare
# ---------------------------------------------------------------------------


def test_count_compare_le_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "01.json").write_text(
        json.dumps([1, 2, 3, 4]), encoding="utf-8",
    )
    (ctx.artifacts_base / "02.json").write_text(
        json.dumps([1, 2, 3]), encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="count_compare",
        left={"path": "02.json", "field": "$[*]"},
        right={"path": "01.json", "field": "$[*]"},
        compare_op="le",
    )], ctx)
    assert r.status == "pass"
    assert r.detail == {"left_count": 3, "right_count": 4, "op": "le"}


def test_count_compare_le_fail(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "01.json").write_text(
        json.dumps([1, 2]), encoding="utf-8",
    )
    (ctx.artifacts_base / "02.json").write_text(
        json.dumps([1, 2, 3]), encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="count_compare",
        left={"path": "02.json", "field": "$[*]"},
        right={"path": "01.json", "field": "$[*]"},
        compare_op="le",
    )], ctx)
    assert r.status == "fail"


def test_count_compare_const(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "scenes.json").write_text(
        json.dumps({"SCENES": list(range(115))}), encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="count_compare",
        left={"path": "scenes.json", "field": "$.SCENES[*]"},
        right={"const": 115},
        compare_op="eq",
    )], ctx)
    assert r.status == "pass"


# ---------------------------------------------------------------------------
# regex_count
# ---------------------------------------------------------------------------


def test_regex_count_pass(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.artifacts_base / "audioConfig.ts").write_text(
        "export const SCENES = [];\nexport const FPS = 30;\n"
        "export const TOTAL_FRAMES = 3450;\n",
        encoding="utf-8",
    )
    [r] = run_assertions([_spec(
        kind="regex_count", source="file", path="audioConfig.ts",
        pattern=r"^export const \w+", count_min=3,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 3


def test_regex_count_max_fail(tmp_path):
    ctx = _ctx(tmp_path, final_text="A B A B A")
    [r] = run_assertions([_spec(
        kind="regex_count", source="final_text",
        pattern=r"A", count_min=1, count_max=2,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 3


# ---------------------------------------------------------------------------
# error path: unreadable file
# ---------------------------------------------------------------------------


def test_handler_oserror_becomes_error(tmp_path):
    ctx = _ctx(tmp_path)
    [r] = run_assertions([_spec(
        kind="json_valid", path="missing.json",
    )], ctx)
    assert r.status == "error"
    assert r.detail["error_class"] in {"FileNotFoundError", "OSError"}


def test_handler_bad_arg_becomes_error(tmp_path):
    ctx = _ctx(tmp_path)
    [r] = run_assertions([_spec(
        kind="json_field", path="x.json", field=None, op="exists",
    )], ctx)
    assert r.status == "error"
    assert "field" in r.detail["error_message"]


# ---------------------------------------------------------------------------
# merge_assertions / summarize
# ---------------------------------------------------------------------------


def test_merge_appends():
    skill = [_spec(id="a", kind="file_exists", path="x")]
    scenario = [_spec(id="b", kind="file_exists", path="y")]
    out = merge_assertions(skill, scenario, [])
    assert [a.id for a in out] == ["a", "b"]


def test_merge_skip_disables_default():
    skill = [_spec(id="a", kind="file_exists", path="x"),
             _spec(id="b", kind="file_exists", path="y")]
    out = merge_assertions(skill, [], ["a"])
    assert [a.id for a in out] == ["b"]


def test_merge_collision_raises():
    skill = [_spec(id="a", kind="file_exists", path="x")]
    scenario = [_spec(id="a", kind="file_exists", path="y")]
    with pytest.raises(ValueError, match="collides"):
        merge_assertions(skill, scenario, [])


def test_merge_collision_resolved_via_skip():
    """skill has 'a', scenario wants to redeclare 'a' — must `skip` first."""
    skill = [_spec(id="a", kind="file_exists", path="x")]
    scenario = [_spec(id="a", kind="file_exists", path="z")]
    out = merge_assertions(skill, scenario, ["a"])
    assert len(out) == 1 and out[0].path == "z"


def test_summarize_counts():
    results = run_assertions(
        [
            _spec(id="ok",   kind="file_absent", path="never-here"),
            _spec(id="bad",  kind="file_exists", path="missing"),
            _spec(id="warn", kind="file_exists", path="missing", blocking=False),
        ],
        AssertionContext(
            sandbox_root=Path("/"), artifacts_base=Path("/"),
            final_text="", stdout="", stderr_tail="",
        ),
    )
    s = summarize(results)
    assert s["total"] == 3
    assert s["passed"] == 1
    assert s["failed"] == 2
    assert s["blocking_failed"] == 1   # only `bad` is blocking
    assert s["all_blocking_passed"] is False


# ---------------------------------------------------------------------------
# tool_call_count (Stage 2.7.J)
# ---------------------------------------------------------------------------


def _ctx_with_calls(tmp_path: Path, calls: list[dict]) -> AssertionContext:
    """Like _ctx but with a preset tool_calls list — for tool_call_count tests."""
    ctx = _ctx(tmp_path)
    ctx.tool_calls = calls
    return ctx


def test_tool_call_count_zero_pass_no_invocations(tmp_path):
    """Primary use case: under session inject, WebFetch should be 0."""
    ctx = _ctx_with_calls(tmp_path, calls=[
        {"name": "Bash", "input": {"command": "ls"}},
        {"name": "Read", "input": {"file_path": "/x"}},
    ])
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="WebFetch", count_max=0,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 0
    assert r.detail["tool_name"] == "WebFetch"


def test_tool_call_count_zero_fail_when_escaped(tmp_path):
    """The smoking gun: cc escaped session inject and made a real WebFetch."""
    ctx = _ctx_with_calls(tmp_path, calls=[
        {"name": "WebFetch", "input": {"url": "https://leaked.example.com"}},
    ])
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="WebFetch", count_max=0,
        blocking=True,
    )], ctx)
    assert r.status == "fail"
    assert r.blocking is True
    assert r.detail["matched_count"] == 1


def test_tool_call_count_range_pass(tmp_path):
    """Budget check: WebFetch called between 2 and 5 times."""
    calls = [{"name": "WebFetch", "input": {}}] * 3 + [{"name": "Bash", "input": {}}]
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="WebFetch",
        count_min=2, count_max=5,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 3
    assert r.detail["min"] == 2
    assert r.detail["max"] == 5


def test_tool_call_count_range_fail_too_many(tmp_path):
    calls = [{"name": "WebFetch", "input": {}}] * 6
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="WebFetch",
        count_min=0, count_max=5,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 6


def test_tool_call_count_min_only_no_upper_bound(tmp_path):
    """count_max=None → no upper bound (just "called at least N times")."""
    calls = [{"name": "Bash", "input": {}}] * 100
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash", count_min=1,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 100
    assert r.detail["max"] is None


def test_tool_call_count_missing_tool_name_errors(tmp_path):
    """tool_name is required — typo'd schema becomes a runtime error rather
    than silently matching zero (which would mask the real bug)."""
    ctx = _ctx_with_calls(tmp_path, calls=[])
    [r] = run_assertions([_spec(
        kind="tool_call_count", count_max=0,  # no tool_name
    )], ctx)
    assert r.status == "error"
    assert "tool_name" in r.detail["error_message"]


# ---------------------------------------------------------------------------
# R-7 治理: exclude is_error tool_use from count (design §16.7.8)
# ---------------------------------------------------------------------------


def test_tool_call_count_excludes_errored_calls_by_default(tmp_path):
    """cc attempted Bash but `--disallowedTools` denied it → count = 0.

    The smoking-gun escape test (count_max=0) must NOT trip when cc
    merely *tried* a forbidden tool and got a tool_use_error back. The
    call never produced a real side effect, so counting it as escape
    is a false positive on P3 双保险.
    """
    ctx = _ctx_with_calls(tmp_path, calls=[
        {"name": "Bash", "input": {"command": "curl evil.com"}, "is_error": True},
        {"name": "Bash", "input": {"command": "ls"}, "is_error": True},
    ])
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash", count_max=0,
        blocking=True,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 0


def test_tool_call_count_still_catches_real_calls(tmp_path):
    """Mixed: one denied + one real → count = 1 (the real one).

    Confirms R-7 doesn't make all P3 assertions toothless — it only
    silences denied attempts, not actual side-effecting calls.
    """
    ctx = _ctx_with_calls(tmp_path, calls=[
        {"name": "Bash", "input": {"command": "denied"}, "is_error": True},
        {"name": "Bash", "input": {"command": "ran-for-real"}, "is_error": False},
    ])
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash", count_max=0,
        blocking=True,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 1


def test_tool_call_count_errored_calls_opt_in_counts_all(tmp_path):
    """count_errored_calls=true → revert to pre-R-7 behaviour (count attempts).

    Edge case where the user wants to detect "cc tried to escape even
    if it was blocked" — useful as a high-signal warning even when no
    side effect occurred.
    """
    ctx = _ctx_with_calls(tmp_path, calls=[
        {"name": "Bash", "input": {"command": "denied-1"}, "is_error": True},
        {"name": "Bash", "input": {"command": "denied-2"}, "is_error": True},
    ])
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash", count_max=0,
        count_errored_calls=True,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 2


def test_tool_call_count_handles_missing_is_error_field(tmp_path):
    """Old tool_call dicts without is_error key are treated as is_error=False.

    Provider versions before R-7 didn't emit is_error on tool_calls;
    those should keep counting (default-truthy is False, missing key
    means "no signal about error", which we conservatively interpret
    as a successful call for back-compat).
    """
    ctx = _ctx_with_calls(tmp_path, calls=[
        {"name": "Bash", "input": {"command": "old-record-no-is-error"}},
    ])
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash", count_max=0,
        blocking=True,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 1


# ---------------------------------------------------------------------------
# tool_call_count + tool_input_match (M5 — F-07 / F-10)
# ---------------------------------------------------------------------------


def test_tool_input_match_regex_filters_count(tmp_path):
    """F-07: cc tried to grade artifacts via `python score.py`; tool_input_match
    catches it even though Bash itself is legitimate."""
    calls = [
        {"name": "Bash", "input": {"command": "ls -la"}},
        {"name": "Bash", "input": {"command": "python score.py --in 03.json"}},
        {"name": "Bash", "input": {"command": "git status"}},
        {"name": "Bash", "input": {"command": "node grade.js"}},
    ]
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash",
        tool_input_match={"field": "command", "op": "regex",
                          "pattern": r"(python|node).*(score|grade)"},
        count_max=0, blocking=True,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["matched_count"] == 2
    assert r.detail["filter"]["op"] == "regex"
    assert r.detail["filter"]["field"] == "command"


def test_tool_input_match_contains_substring(tmp_path):
    """op=contains: simple substring filter without regex syntax."""
    calls = [
        {"name": "WebFetch", "input": {"url": "https://news.ycombinator.com/news"}},
        {"name": "WebFetch", "input": {"url": "https://example.com/blog"}},
        {"name": "WebFetch", "input": {"url": "https://news.ycombinator.com/show"}},
    ]
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="WebFetch",
        tool_input_match={"field": "url", "op": "contains",
                          "pattern": "ycombinator.com"},
        count_min=2,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 2


def test_tool_input_match_eq_exact(tmp_path):
    """op=eq: stringified exact match."""
    calls = [
        {"name": "Bash", "input": {"command": "ls"}},
        {"name": "Bash", "input": {"command": "ls -la"}},
        {"name": "Bash", "input": {"command": "ls"}},
    ]
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash",
        tool_input_match={"field": "command", "op": "eq", "pattern": "ls"},
        count_min=2, count_max=2,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 2


def test_tool_input_match_ne_counts_non_matches(tmp_path):
    """op=ne: count Bash calls whose command is NOT the allowed one."""
    calls = [
        {"name": "Bash", "input": {"command": "git status"}},
        {"name": "Bash", "input": {"command": "git diff"}},
        {"name": "Bash", "input": {"command": "rm -rf /"}},  # the bad one
    ]
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash",
        tool_input_match={"field": "command", "op": "ne", "pattern": "git status"},
        count_max=1,  # at most one "non-allowed" command
    )], ctx)
    # Two calls are "not equal to git status" → fail
    assert r.status == "fail"
    assert r.detail["matched_count"] == 2


def test_tool_input_match_nested_field_errors(tmp_path):
    """MVP doesn't support `a.b` dotted paths; surface friendly error."""
    ctx = _ctx_with_calls(tmp_path, calls=[
        {"name": "Bash", "input": {"command": "ls"}},
    ])
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash",
        tool_input_match={"field": "args.command", "op": "regex", "pattern": ".*"},
        count_max=0,
    )], ctx)
    assert r.status == "error"
    assert "nested" in r.detail["error_message"].lower()


def test_tool_input_match_missing_field_no_match(tmp_path):
    """If the input dict has no `field` key, the call does NOT count toward
    the total (per ToolInputMatchSpec docstring)."""
    calls = [
        {"name": "Bash", "input": {"description": "ran something"}},  # no `command`
        {"name": "Bash", "input": {"command": "python score.py"}},
    ]
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash",
        tool_input_match={"field": "command", "op": "regex", "pattern": r"python"},
        count_max=2,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 1  # only the second call has a command


def test_tool_call_count_without_filter_backward_compat(tmp_path):
    """Backward-compat: omitting tool_input_match keeps original behaviour
    (count every invocation of tool_name regardless of input shape)."""
    calls = [
        {"name": "Bash", "input": {"command": "ls"}},
        {"name": "Bash", "input": {"command": "pwd"}},
        {"name": "Read", "input": {"file_path": "/x"}},
    ]
    ctx = _ctx_with_calls(tmp_path, calls=calls)
    [r] = run_assertions([_spec(
        kind="tool_call_count", tool_name="Bash", count_min=2, count_max=2,
    )], ctx)
    assert r.status == "pass"
    assert r.detail["matched_count"] == 2
    assert "filter" not in r.detail  # no filter applied → no filter detail


# ---------------------------------------------------------------------------
# cross_file_consistency (M5 — F-06)
# ---------------------------------------------------------------------------


def _write(base: Path, rel: str, body: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_cross_file_subset_regex_to_jsonpath_pass(tmp_path):
    """F-06 happy path: every URL in 04-report.md appears in 03-evaluated.json."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "04-report.md", "see https://a.example and https://b.example")
    _write(ctx.artifacts_base, "03-evaluated.json",
           json.dumps([{"url": "https://a.example"},
                       {"url": "https://b.example"},
                       {"url": "https://c.example"}]))
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "04-report.md", "extract": "regex",
                "pattern": r"https?://\S+"},
        target={"file": "03-evaluated.json", "extract": "jsonpath",
                "pattern": "$[*].url"},
        relation="source_subset_of_target",
    )], ctx)
    assert r.status == "pass"
    assert r.detail["source_count"] == 2
    assert r.detail["target_count"] == 3


def test_cross_file_subset_regex_to_jsonpath_fail(tmp_path):
    """Report cites a URL that's NOT in the evaluated list → fail."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "04-report.md",
           "see https://a.example and https://orphan.example")
    _write(ctx.artifacts_base, "03-evaluated.json",
           json.dumps([{"url": "https://a.example"}]))
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "04-report.md", "extract": "regex",
                "pattern": r"https?://\S+"},
        target={"file": "03-evaluated.json", "extract": "jsonpath",
                "pattern": "$[*].url"},
        relation="source_subset_of_target",
        blocking=False,
    )], ctx)
    assert r.status == "fail"
    assert r.detail["missing_count"] == 1
    assert "https://orphan.example" in r.detail["missing_sample"]


def test_cross_file_target_subset_lines_to_regex_pass(tmp_path):
    """Reverse relation + lines extract: every line in topics.txt appears in script."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "topics.txt", "agents\nrag\nmemory\n")
    _write(ctx.artifacts_base, "01-script.md",
           "intro mentions agents, then rag, finally memory and beyond")
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "01-script.md", "extract": "regex",
                "pattern": r"\b(agents|rag|memory)\b"},
        target={"file": "topics.txt", "extract": "lines"},
        relation="target_subset_of_source",
    )], ctx)
    assert r.status == "pass"


def test_cross_file_equal_lines_pass(tmp_path):
    """source_equal_target with lines extract: identical value sets."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "a.txt", "x\ny\nz\n")
    _write(ctx.artifacts_base, "b.txt", "y\nx\nz")  # same set, different order
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "a.txt", "extract": "lines"},
        target={"file": "b.txt", "extract": "lines"},
        relation="source_equal_target",
    )], ctx)
    assert r.status == "pass"


def test_cross_file_equal_lines_fail_both_directions(tmp_path):
    """source_equal_target reports missing items from both sides."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "a.txt", "x\ny\n")
    _write(ctx.artifacts_base, "b.txt", "y\nz\n")
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "a.txt", "extract": "lines"},
        target={"file": "b.txt", "extract": "lines"},
        relation="source_equal_target",
    )], ctx)
    assert r.status == "fail"
    assert r.detail["missing_count"] == 2  # x missing in b, z missing in a
    assert set(r.detail["missing_sample"]) >= {"x", "z"}


def test_cross_file_on_miss_skip_downgrades_to_pass(tmp_path):
    """on_miss=skip: relation violation still records detail but returns pass."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "04-report.md", "https://orphan.example")
    _write(ctx.artifacts_base, "03-evaluated.json",
           json.dumps([{"url": "https://other.example"}]))
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "04-report.md", "extract": "regex",
                "pattern": r"https?://\S+"},
        target={"file": "03-evaluated.json", "extract": "jsonpath",
                "pattern": "$[*].url"},
        relation="source_subset_of_target",
        on_miss="skip",
    )], ctx)
    assert r.status == "pass"
    assert r.detail["missing_count"] == 1
    assert r.detail["downgraded"] == "skip → pass"


def test_cross_file_on_miss_abort_becomes_error(tmp_path):
    """on_miss=abort: relation violation escalates to error status."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "a.txt", "x\n")
    _write(ctx.artifacts_base, "b.txt", "y\n")
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "a.txt", "extract": "lines"},
        target={"file": "b.txt", "extract": "lines"},
        relation="source_subset_of_target",
        on_miss="abort",
    )], ctx)
    assert r.status == "error"
    assert "abort" in r.detail["error_message"].lower()


def test_cross_file_empty_source_passes_trivially(tmp_path):
    """Empty source (regex matches nothing) → trivially subset → pass."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "report.md", "no urls here at all")
    _write(ctx.artifacts_base, "evaluated.json",
           json.dumps([{"url": "https://a"}]))
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "report.md", "extract": "regex",
                "pattern": r"https?://\S+"},
        target={"file": "evaluated.json", "extract": "jsonpath",
                "pattern": "$[*].url"},
        relation="source_subset_of_target",
    )], ctx)
    assert r.status == "pass"
    assert r.detail["source_count"] == 0


def test_cross_file_missing_file_becomes_error(tmp_path):
    """File not found → handler raises → status='error' (not 'fail')."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "evaluated.json",
           json.dumps([{"url": "https://a"}]))
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "missing.md", "extract": "regex", "pattern": r"\S+"},
        target={"file": "evaluated.json", "extract": "jsonpath",
                "pattern": "$[*].url"},
        relation="source_subset_of_target",
    )], ctx)
    assert r.status == "error"
    assert r.detail["error_class"] in {"FileNotFoundError", "OSError"}


def test_cross_file_jsonpath_no_hit_empty_target(tmp_path):
    """jsonpath returns 0 leaves → target_count=0; subset of empty fails."""
    ctx = _ctx(tmp_path)
    _write(ctx.artifacts_base, "report.md", "see https://a")
    _write(ctx.artifacts_base, "evaluated.json",
           json.dumps([{"title": "no url field here"}]))
    [r] = run_assertions([_spec(
        kind="cross_file_consistency",
        source={"file": "report.md", "extract": "regex",
                "pattern": r"https?://\S+"},
        target={"file": "evaluated.json", "extract": "jsonpath",
                "pattern": "$[*].url"},
        relation="source_subset_of_target",
    )], ctx)
    assert r.status == "fail"
    assert r.detail["target_count"] == 0
    assert r.detail["missing_count"] == 1
