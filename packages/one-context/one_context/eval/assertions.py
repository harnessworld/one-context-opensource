"""Declarative assertions DSL — runs BEFORE the LLM rubric judge.

Phase 2.6.B: rubric text was carrying objective conditions ("file must
exist", "JSON weighted sum within ±2", "report ends with literal X")
alongside subjective ones ("recommended topic list is semantically
sound"). The objective half belongs in code so judging is deterministic
and zero-token; the subjective half stays with the LLM.

Each scenario merges `skill_cfg.assertions` (defaults) with
`scen_cfg.assertions` (additive) and `scen_cfg.assertions_skip`
(reverse-disable a default by id). Scenario ids must NOT collide with
the skill defaults — collision is a config error, not a silent override.

The runner runs every assertion (no short-circuit, so the report shows
the full picture even on failure) then short-circuits the LLM judge if
any `blocking=True` assertion did not pass. Non-blocking failures are
recorded but do NOT block the judge — that is the "advisory" lane.

DSL kinds (11):
  - file_exists / file_absent — single path under sandbox root
  - path_absent — fnmatch glob, sandbox-wide path forbidden
  - text_contains / text_absent — substring/regex against a file,
    `final_text`, or `stderr_tail`
  - json_valid — JSON parses
  - json_field — single-file probe with op = exists / eq / in_range /
    type / unique / all_match (mini-jsonpath: $/.field/[*]/[N])
  - count_compare — cross-file array length compare (le/lt/eq/ge/gt),
    right side may be {const: int} for fixed targets
  - regex_count — number of regex matches in a file/final_text within
    [count_min, count_max]
  - tool_call_count — count tool_use occurrences in stream-json; can
    further filter by `tool_input_match` (e.g. only count Bash calls
    whose `command` matches a regex). Stage 2.7.J + M5.
  - cross_file_consistency — two-file relation check (F-06). Extract
    values from `source` + `target` (extract=regex|jsonpath|lines),
    then verify `relation` (source_subset_of_target /
    target_subset_of_source / source_equal_target). `on_miss` =
    report (default, fail with detail) / skip (pass anyway) / abort
    (raise as error). M5.
"""

from __future__ import annotations

import fnmatch
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Pydantic spec
# ---------------------------------------------------------------------------


KIND_LITERAL = Literal[
    "file_exists",
    "file_absent",
    "path_absent",
    "text_contains",
    "text_absent",
    "json_valid",
    "json_field",
    "count_compare",
    "regex_count",
    "tool_call_count",  # ISS-024 / Stage 2.7.J: verify session inject didn't leak
    "cross_file_consistency",  # M5: F-06 推荐 URL 在 03 里
]
SOURCE_LITERAL = Literal["file", "stdout", "final_text", "stderr_tail"]
JSON_OP_LITERAL = Literal[
    "exists", "eq", "in_range", "type", "unique", "all_match"
]
COMPARE_OP_LITERAL = Literal["le", "lt", "eq", "ge", "gt"]
EXPECTED_TYPE_LITERAL = Literal[
    "string", "number", "integer", "boolean", "array", "object", "null"
]
EXTRACT_LITERAL = Literal["regex", "jsonpath", "lines"]
RELATION_LITERAL = Literal[
    "source_subset_of_target",
    "target_subset_of_source",
    "source_equal_target",
]
ON_MISS_LITERAL = Literal["report", "skip", "abort"]
TOOL_INPUT_OP_LITERAL = Literal["regex", "contains", "eq", "ne"]


class CrossFileSide(BaseModel):
    """M5: one side (source / target) of a `cross_file_consistency` check.

    `file` resolves under `artifacts_base` (target_path-relative, same
    rules as file_exists). `extract` picks the value-extraction mode:
      - regex    — `re.findall(pattern, file_text, re.MULTILINE)` and
                   the matches are the values.
      - jsonpath — parse file as JSON, then mini-jsonpath
                   (`_resolve_jsonpath`) — pattern is the expression
                   (e.g. `$[*].url`).
      - lines    — split file text by newline, strip empties; pattern
                   is ignored (kept as `""` per convention).
    """
    model_config = ConfigDict(extra="forbid")

    file: str
    extract: EXTRACT_LITERAL
    pattern: str = ""


class ToolInputMatchSpec(BaseModel):
    """M5: optional filter on `tool_call_count` (F-07 / F-10).

    Without `tool_input_match`, tool_call_count behaves as before
    (matches any invocation of `tool_name`). With it, only those calls
    whose `input[field]` matches the predicate count toward the total.

    `field` MVP is a single key (no dotted path). Nested paths raise a
    friendly runtime error so authors don't silently get a wrong count.

    `op` semantics on the stringified input field value:
      - regex    — `re.search(pattern, str(value))` truthy
      - contains — `pattern in str(value)`
      - eq       — `str(value) == pattern`
      - ne       — `str(value) != pattern`

    If the input dict has no `field` key at all, the call does NOT
    match (count is not incremented) regardless of `op` — silent absence
    is treated as "filter didn't fire" rather than as "value is empty".
    """
    model_config = ConfigDict(extra="forbid")

    field: str
    op: TOOL_INPUT_OP_LITERAL
    pattern: str


class AssertionSpec(BaseModel):
    """One assertion entry in `eval.yaml: assertions:` or `scenario.yaml`.

    `id` MUST be unique within the merged (skill + scenario) list. The
    runtime dispatch validates that the kind-specific fields needed by
    each `kind` are filled — pydantic alone can't express this without
    a discriminated union per kind, and the union form makes the YAML
    surface noisier. Trade-off: a typo in a kind-specific field is
    surfaced at run time as `status="error"` rather than at parse time.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: KIND_LITERAL
    blocking: bool = True
    message: str = ""

    # path/glob for file-shaped kinds
    path: str | None = None
    glob: str | None = None

    # text_*/regex_count: where to look + what to look for. M5: the
    # `source` field now ALSO carries the source side of a
    # `cross_file_consistency` spec — when given a dict, pydantic
    # validates it as a CrossFileSide; when given a string, it remains
    # the original SOURCE_LITERAL. Mutually exclusive at runtime
    # (the kind handler reads only the shape it expects).
    source: CrossFileSide | SOURCE_LITERAL | None = None
    needle: str | None = None
    pattern: str | None = None
    regex: bool = False
    count_min: int | None = None
    count_max: int | None = None

    # json_field: probe a single document
    field: str | None = None
    op: JSON_OP_LITERAL | None = None
    value: Any = None
    min: float | None = None
    max: float | None = None
    expected_type: EXPECTED_TYPE_LITERAL | None = None

    # count_compare: two-sided
    left: dict[str, Any] | None = None
    right: dict[str, Any] | None = None
    compare_op: COMPARE_OP_LITERAL | None = None

    # tool_call_count (ISS-024 / Stage 2.7.J): cc tool name to count occurrences
    # of in run.json.tool_calls. Reuses count_min / count_max for range bounds
    # (regex_count-style). Typical pattern under session inject:
    #   tool_name: WebFetch, count_min: 0, count_max: 0  →  blocking fail if
    #   cc "escaped" and made a real WebFetch call despite the forged history.
    tool_name: str | None = None
    # M5: optional filter on tool_call_count (F-07 / F-10). Backward
    # compatible — when omitted, tool_call_count behaviour is unchanged.
    tool_input_match: ToolInputMatchSpec | None = None

    # R-7 治理 (design §16.7.8): by default tool_call_count excludes
    # tool_use events whose paired tool_result was `is_error=True`
    # (cc-attempted-but-denied, e.g. `--disallowedTools` deny). Set
    # `count_errored_calls: true` to revert to pre-R-7 behaviour (count
    # every attempt). Pre-R-7 scenarios are unaffected because the
    # provider always emits `is_error: false` on successful calls and
    # the bit only matters when the assertion is a P3-style 0-count.
    count_errored_calls: bool = False

    # cross_file_consistency (M5 — F-06). The `source` side reuses the
    # `source` field above (Union with CrossFileSide). `target`,
    # `relation`, `on_miss` are new and only used by this kind.
    target: CrossFileSide | None = None
    relation: RELATION_LITERAL | None = None
    on_miss: ON_MISS_LITERAL = "report"


# ---------------------------------------------------------------------------
# Runtime context + result
# ---------------------------------------------------------------------------


@dataclass
class AssertionContext:
    """Everything an assertion handler can inspect.

    `artifacts_base` resolves under `sandbox_root / target_path` so paths
    in YAML can be written relative to the feature subtree (the way a
    user thinks about them). `produced_paths` is the set of relative
    paths the post-snapshot diff said were added/changed by the run —
    used for "did the skill actually create file X" without re-walking
    the disk.
    """

    sandbox_root: Path
    artifacts_base: Path
    final_text: str
    stdout: str
    stderr_tail: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    produced_paths: set[str] = field(default_factory=set)


@dataclass
class AssertionResult:
    id: str
    kind: str
    status: Literal["pass", "fail", "error"]
    blocking: bool
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Mini JSONPath  ($, .field, [*], [N])
# ---------------------------------------------------------------------------


def _tokenize_jsonpath(expr: str) -> list[tuple[str, Any]]:
    r"""Return tokens like [("key","articles"), ("wild",None), ("key","url")].

    Grammar:
      path  := "$" segment*
      segment := "." key  |  "[" "*" "]"  |  "[" int "]"
      key   := [^.[\]]+
    """
    if not expr:
        raise ValueError("empty jsonpath")
    s = expr.strip()
    if not s.startswith("$"):
        raise ValueError(f"jsonpath must start with $: {expr!r}")
    s = s[1:]
    tokens: list[tuple[str, Any]] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == ".":
            j = i + 1
            if j >= n or s[j] in ".[":
                raise ValueError(f"jsonpath empty key at {i}: {expr!r}")
            while j < n and s[j] not in ".[":
                j += 1
            tokens.append(("key", s[i + 1 : j]))
            i = j
        elif c == "[":
            close = s.find("]", i)
            if close < 0:
                raise ValueError(f"jsonpath unclosed bracket: {expr!r}")
            inside = s[i + 1 : close].strip()
            if inside == "*":
                tokens.append(("wild", None))
            else:
                try:
                    tokens.append(("idx", int(inside)))
                except ValueError as e:
                    raise ValueError(
                        f"jsonpath unsupported bracket content {inside!r}: {expr!r}"
                    ) from e
            i = close + 1
        else:
            raise ValueError(f"jsonpath unexpected char {c!r} at {i}: {expr!r}")
    return tokens


def _resolve_jsonpath(value: Any, expr: str) -> list[Any]:
    """Resolve `expr` against `value` and return all matching leaves.

    Missing keys / out-of-bounds indices yield no result (empty list)
    rather than raising — handlers decide whether absence is a fail or
    not (`json_field op=exists` checks length>=1; `op=in_range` against
    an empty result is also fail).
    """
    tokens = _tokenize_jsonpath(expr)
    current: list[Any] = [value]
    for kind, val in tokens:
        nxt: list[Any] = []
        for v in current:
            if kind == "key":
                if isinstance(v, dict) and val in v:
                    nxt.append(v[val])
            elif kind == "wild":
                if isinstance(v, list):
                    nxt.extend(v)
                elif isinstance(v, dict):
                    nxt.extend(v.values())
            elif kind == "idx":
                if isinstance(v, list) and -len(v) <= val < len(v):
                    nxt.append(v[val])
        current = nxt
    return current


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _matches_type(v: Any, expected: str) -> bool:
    if expected == "number":
        # number covers int + float (but not bool — bool is its own thing)
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return _type_name(v) == expected


# ---------------------------------------------------------------------------
# Source resolution helpers
# ---------------------------------------------------------------------------


def _resolve_source_text(spec: AssertionSpec, ctx: AssertionContext) -> str:
    if isinstance(spec.source, CrossFileSide):
        raise ValueError(
            f"{spec.kind}: `source` is a CrossFileSide mapping; that shape "
            "only applies to kind=cross_file_consistency. For text_*/regex_count "
            "use source: file|stdout|final_text|stderr_tail."
        )
    src = spec.source or "file"
    if src == "final_text":
        return ctx.final_text or ""
    if src == "stdout":
        return ctx.stdout or ""
    if src == "stderr_tail":
        return ctx.stderr_tail or ""
    if src == "file":
        if not spec.path:
            raise ValueError(f"{spec.kind}: source=file requires `path`")
        return _read_file(ctx.artifacts_base, spec.path)
    raise ValueError(f"unknown source: {src}")


def _read_file(base: Path, rel: str) -> str:
    """Read a file resolved from `artifacts_base` (target_path-relative)
    or from sandbox root if `rel` looks absolute / starts with sandbox.
    """
    full = (base / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
    return full.read_text(encoding="utf-8", errors="replace")


def _read_json(base: Path, rel: str) -> Any:
    text = _read_file(base, rel)
    return json.loads(text)


def _array_count(value: Any, field_expr: str) -> int:
    """Resolve `field_expr` against `value` and return the count of leaves."""
    leaves = _resolve_jsonpath(value, field_expr)
    return len(leaves)


# ---------------------------------------------------------------------------
# Kind handlers
# ---------------------------------------------------------------------------


def _h_file_exists(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if not spec.path:
        raise ValueError("file_exists: `path` required")
    full = (ctx.artifacts_base / spec.path).resolve()
    detail = {"resolved": str(full)}
    if full.is_file():
        detail["size"] = full.stat().st_size
        return "pass", detail
    return "fail", detail


def _h_file_absent(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if not spec.path:
        raise ValueError("file_absent: `path` required")
    full = (ctx.artifacts_base / spec.path).resolve()
    detail = {"resolved": str(full)}
    if not full.exists():
        return "pass", detail
    detail["size"] = full.stat().st_size if full.is_file() else None
    return "fail", detail


def _h_path_absent(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    """Sandbox-wide check: NO file matches `glob`. Walks the sandbox root."""
    if not spec.glob:
        raise ValueError("path_absent: `glob` required")
    matches: list[str] = []
    root = ctx.sandbox_root
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        if fnmatch.fnmatch(rel, spec.glob):
            matches.append(rel)
            if len(matches) >= 20:
                break
    detail = {"matches": matches, "match_count": len(matches)}
    return ("pass", detail) if not matches else ("fail", detail)


def _h_text_contains(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if spec.needle is None and spec.pattern is None:
        raise ValueError("text_contains: `needle` or `pattern` required")
    text = _resolve_source_text(spec, ctx)
    if spec.regex or spec.pattern:
        pat = spec.pattern if spec.pattern is not None else spec.needle
        matches = re.findall(pat, text, flags=re.MULTILINE)
        count = len(matches)
    else:
        count = text.count(spec.needle or "")
    floor = spec.count_min if spec.count_min is not None else 1
    detail = {"matched_count": count, "min": floor}
    if spec.count_max is not None:
        detail["max"] = spec.count_max
    ok = count >= floor and (spec.count_max is None or count <= spec.count_max)
    return ("pass", detail) if ok else ("fail", detail)


def _h_text_absent(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if spec.needle is None and spec.pattern is None:
        raise ValueError("text_absent: `needle` or `pattern` required")
    text = _resolve_source_text(spec, ctx)
    if spec.regex or spec.pattern:
        pat = spec.pattern if spec.pattern is not None else spec.needle
        matches = re.findall(pat, text, flags=re.MULTILINE)
        count = len(matches)
    else:
        count = text.count(spec.needle or "")
    detail = {"matched_count": count}
    return ("pass", detail) if count == 0 else ("fail", detail)


def _h_json_valid(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if not spec.path:
        raise ValueError("json_valid: `path` required")
    _read_json(ctx.artifacts_base, spec.path)
    return "pass", {"path": spec.path}


def _h_json_field(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if not spec.path:
        raise ValueError("json_field: `path` required")
    if not spec.field:
        raise ValueError("json_field: `field` required")
    op = spec.op or "exists"
    doc = _read_json(ctx.artifacts_base, spec.path)
    leaves = _resolve_jsonpath(doc, spec.field)

    if op == "exists":
        ok = len(leaves) >= 1
        return ("pass" if ok else "fail"), {"matched_count": len(leaves)}

    if op == "eq":
        if len(leaves) != 1:
            return "fail", {"matched_count": len(leaves), "expected_singleton": True}
        ok = leaves[0] == spec.value
        return ("pass" if ok else "fail"), {"actual": leaves[0], "expected": spec.value}

    if op == "in_range":
        if not leaves:
            return "fail", {"matched_count": 0}
        out_of_range = [
            v for v in leaves
            if not (isinstance(v, (int, float)) and not isinstance(v, bool))
            or (spec.min is not None and v < spec.min)
            or (spec.max is not None and v > spec.max)
        ]
        ok = not out_of_range
        return ("pass" if ok else "fail"), {
            "checked": len(leaves),
            "out_of_range_count": len(out_of_range),
            "out_of_range_sample": out_of_range[:5],
            "min": spec.min, "max": spec.max,
        }

    if op == "type":
        if not spec.expected_type:
            raise ValueError("json_field op=type requires `expected_type`")
        bad = [v for v in leaves if not _matches_type(v, spec.expected_type)]
        ok = bool(leaves) and not bad
        return ("pass" if ok else "fail"), {
            "checked": len(leaves),
            "wrong_type_count": len(bad),
            "wrong_type_sample": [_type_name(v) for v in bad[:5]],
            "expected_type": spec.expected_type,
        }

    if op == "unique":
        try:
            uniq = set(leaves)
        except TypeError:
            uniq = {json.dumps(v, sort_keys=True, ensure_ascii=False) for v in leaves}
        ok = len(uniq) == len(leaves)
        return ("pass" if ok else "fail"), {
            "total": len(leaves), "unique": len(uniq),
            "duplicates": len(leaves) - len(uniq),
        }

    if op == "all_match":
        if not spec.value:
            raise ValueError("json_field op=all_match requires `value` (regex string)")
        rx = re.compile(spec.value)
        bad = [v for v in leaves if not (isinstance(v, str) and rx.search(v))]
        ok = bool(leaves) and not bad
        return ("pass" if ok else "fail"), {
            "checked": len(leaves),
            "mismatched_count": len(bad),
            "mismatched_sample": bad[:5],
            "pattern": spec.value,
        }

    raise ValueError(f"json_field: unsupported op={op!r}")


def _h_count_compare(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if not spec.left or not spec.right:
        raise ValueError("count_compare: both `left` and `right` required")
    if not spec.compare_op:
        raise ValueError("count_compare: `compare_op` required")
    left_count = _resolve_count_side(spec.left, ctx)
    right_count = _resolve_count_side(spec.right, ctx)
    ops: dict[str, Callable[[int, int], bool]] = {
        "le": lambda a, b: a <= b,
        "lt": lambda a, b: a < b,
        "eq": lambda a, b: a == b,
        "ge": lambda a, b: a >= b,
        "gt": lambda a, b: a > b,
    }
    ok = ops[spec.compare_op](left_count, right_count)
    return ("pass" if ok else "fail"), {
        "left_count": left_count,
        "right_count": right_count,
        "op": spec.compare_op,
    }


def _resolve_count_side(side: dict[str, Any], ctx: AssertionContext) -> int:
    if "const" in side:
        return int(side["const"])
    if "path" not in side or "field" not in side:
        raise ValueError(
            "count_compare side requires {path, field} or {const}; got "
            f"{sorted(side.keys())}"
        )
    doc = _read_json(ctx.artifacts_base, side["path"])
    return _array_count(doc, side["field"])


def _h_regex_count(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    if not spec.pattern:
        raise ValueError("regex_count: `pattern` required")
    text = _resolve_source_text(spec, ctx)
    matches = re.findall(spec.pattern, text, flags=re.MULTILINE)
    count = len(matches)
    floor = spec.count_min if spec.count_min is not None else 0
    ceil = spec.count_max
    ok = count >= floor and (ceil is None or count <= ceil)
    return ("pass" if ok else "fail"), {
        "matched_count": count, "min": floor, "max": ceil,
    }


def _h_tool_call_count(spec: AssertionSpec, ctx: AssertionContext) -> tuple[str, dict]:
    """ISS-024 / Stage 2.7.J: count how many tool_calls invoked `tool_name`.

    Pass when count ∈ [count_min, count_max]. count_min defaults to 0
    (so `tool_call_count: count_max: 0` is shorthand for "tool MUST NOT
    be called"). count_max=None means no upper bound.

    Primary use: under session inject, set count_max=0 to assert that cc
    did not "escape" the forged history and invoke the real tool. Also
    useful for budget checks ("MUST call WebFetch at most 5 times").

    M5 — `tool_input_match` (optional): further filter calls by an
    input-field predicate (F-07 / F-10). When set, only invocations of
    `tool_name` whose `input[field]` passes the predicate count toward
    the total. See ToolInputMatchSpec docstring for op semantics. When
    omitted, behaviour is unchanged (backward compatible).

    R-7 治理 (design §16.7.8): by default tool_use events whose paired
    tool_result is `is_error=True` are EXCLUDED from the count. These
    are cc-attempted-but-denied calls (e.g. --disallowedTools deny:
    `tool_use_error: Bash is not enabled in this context`) — the call
    never produced a real-world side effect, so counting them as
    "escape" gives false positives on P3 double-insurance. Pass
    `count_errored_calls: true` in the assertion spec to revert to the
    pre-R-7 behaviour (count all attempts including denied ones).
    """
    if not spec.tool_name:
        raise ValueError("tool_call_count: `tool_name` required")
    matching = [tc for tc in ctx.tool_calls if tc.get("name") == spec.tool_name]
    if not spec.count_errored_calls:
        matching = [tc for tc in matching if not tc.get("is_error")]
    filter_used = spec.tool_input_match is not None
    if filter_used:
        matching = [tc for tc in matching if _tool_input_matches(tc, spec.tool_input_match)]
    count = len(matching)
    floor = spec.count_min if spec.count_min is not None else 0
    ceil = spec.count_max
    ok = count >= floor and (ceil is None or count <= ceil)
    detail: dict[str, Any] = {
        "tool_name":     spec.tool_name,
        "matched_count": count,
        "min":           floor,
        "max":           ceil,
    }
    if filter_used:
        tim = spec.tool_input_match
        detail["filter"] = {"field": tim.field, "op": tim.op, "pattern": tim.pattern}
    return ("pass" if ok else "fail"), detail


def _tool_input_matches(tc: dict[str, Any], tim: ToolInputMatchSpec) -> bool:
    """Apply a ToolInputMatchSpec predicate to one tool_call dict.

    MVP: `tim.field` must be a single key — nested dotted paths like
    `a.b` raise ValueError so authors notice the missing capability
    instead of silently always-mismatching.

    Missing field key → no match (count is not incremented). This is
    deliberate: if cc didn't send the field, we can't say anything
    about its value, so we treat it as "filter didn't fire" rather
    than as "value is empty".
    """
    if "." in tim.field:
        raise ValueError(
            f"tool_input_match.field={tim.field!r}: nested dotted paths "
            "not supported in MVP (only top-level keys; e.g. 'command' / "
            "'url'). Open an issue if you need nested access."
        )
    inp = tc.get("input") if isinstance(tc.get("input"), dict) else {}
    if tim.field not in inp:
        return False
    value_str = str(inp[tim.field])
    if tim.op == "regex":
        try:
            return re.search(tim.pattern, value_str) is not None
        except re.error as e:
            raise ValueError(f"tool_input_match: bad regex {tim.pattern!r}: {e}") from e
    if tim.op == "contains":
        return tim.pattern in value_str
    if tim.op == "eq":
        return value_str == tim.pattern
    if tim.op == "ne":
        return value_str != tim.pattern
    raise ValueError(f"tool_input_match: unsupported op={tim.op!r}")


def _h_cross_file_consistency(
    spec: AssertionSpec, ctx: AssertionContext,
) -> tuple[str, dict]:
    """M5 — F-06: assert a value-set relation across two files.

    Extracts values from `source` and `target` per their `extract` mode
    (regex / jsonpath / lines), then enforces `relation`:
      - source_subset_of_target: every source value appears in target
      - target_subset_of_source: every target value appears in source
      - source_equal_target: both sides have identical value sets

    `on_miss` controls behaviour when there's a relation violation:
      - report (default) — status="fail", detail lists missing items
      - skip             — status="pass" anyway, missing items in detail
      - abort            — raise ValueError → status="error"
    """
    if not isinstance(spec.source, CrossFileSide):
        raise ValueError(
            "cross_file_consistency: `source` must be a mapping with "
            "{file, extract, pattern}"
        )
    if not isinstance(spec.target, CrossFileSide):
        raise ValueError(
            "cross_file_consistency: `target` must be a mapping with "
            "{file, extract, pattern}"
        )
    if not spec.relation:
        raise ValueError(
            "cross_file_consistency: `relation` required "
            "(source_subset_of_target / target_subset_of_source / source_equal_target)"
        )

    src_vals = _extract_side_values(spec.source, ctx)
    tgt_vals = _extract_side_values(spec.target, ctx)
    src_set, tgt_set = set(src_vals), set(tgt_vals)

    missing_in_target = sorted(src_set - tgt_set)
    missing_in_source = sorted(tgt_set - src_set)

    if spec.relation == "source_subset_of_target":
        ok = not missing_in_target
        missing = missing_in_target
    elif spec.relation == "target_subset_of_source":
        ok = not missing_in_source
        missing = missing_in_source
    else:  # source_equal_target
        ok = not missing_in_target and not missing_in_source
        missing = missing_in_target + missing_in_source

    detail: dict[str, Any] = {
        "source_count":  len(src_vals),
        "target_count":  len(tgt_vals),
        "relation":      spec.relation,
        "missing_count": 0 if ok else len(missing),
        "missing_sample": [] if ok else missing[:10],
        "on_miss":       spec.on_miss,
    }
    if ok:
        return "pass", detail

    if spec.on_miss == "skip":
        detail["downgraded"] = "skip → pass"
        return "pass", detail
    if spec.on_miss == "abort":
        raise ValueError(
            f"cross_file_consistency: on_miss=abort and relation={spec.relation} "
            f"failed; missing={detail['missing_sample']}"
        )
    # on_miss == "report" (default)
    return "fail", detail


def _extract_side_values(side: CrossFileSide, ctx: AssertionContext) -> list[str]:
    """Read `side.file` (artifacts_base-relative) and extract value list.

    Returns stringified values so set-membership in cross_file_consistency
    is well-defined across mixed JSON value types.
    """
    text = _read_file(ctx.artifacts_base, side.file)
    if side.extract == "regex":
        if not side.pattern:
            raise ValueError(
                f"cross_file_consistency side {side.file!r}: "
                "extract=regex requires non-empty `pattern`"
            )
        return [str(m) for m in re.findall(side.pattern, text, flags=re.MULTILINE)]
    if side.extract == "jsonpath":
        if not side.pattern:
            raise ValueError(
                f"cross_file_consistency side {side.file!r}: "
                "extract=jsonpath requires non-empty `pattern`"
            )
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"cross_file_consistency side {side.file!r}: JSON parse failed: {e}"
            ) from e
        return [
            v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False)
            for v in _resolve_jsonpath(doc, side.pattern)
        ]
    if side.extract == "lines":
        return [line.strip() for line in text.splitlines() if line.strip()]
    raise ValueError(f"cross_file_consistency: unknown extract={side.extract!r}")


_HANDLERS: dict[str, Callable[[AssertionSpec, AssertionContext], tuple[str, dict]]] = {
    "file_exists":            _h_file_exists,
    "file_absent":            _h_file_absent,
    "path_absent":            _h_path_absent,
    "text_contains":          _h_text_contains,
    "text_absent":            _h_text_absent,
    "json_valid":             _h_json_valid,
    "json_field":             _h_json_field,
    "count_compare":          _h_count_compare,
    "regex_count":            _h_regex_count,
    "tool_call_count":        _h_tool_call_count,    # Stage 2.7.J
    "cross_file_consistency": _h_cross_file_consistency,  # M5
}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def merge_assertions(
    skill: list[AssertionSpec],
    scenario: list[AssertionSpec],
    skip_ids: list[str],
) -> list[AssertionSpec]:
    """Merge skill defaults + scenario additions, honoring `assertions_skip`.

    Raises ValueError if a scenario `id` collides with a (post-skip) skill
    default; collisions are config errors because the failure-mode of a
    silent override is loud regressions during eval changes.
    """
    skip = set(skip_ids)
    surviving = [a for a in skill if a.id not in skip]
    surviving_ids = {a.id for a in surviving}
    for s in scenario:
        if s.id in surviving_ids:
            raise ValueError(
                f"scenario assertion id={s.id!r} collides with a skill default "
                "(use assertions_skip to disable the default first, then redeclare)"
            )
        surviving.append(s)
        surviving_ids.add(s.id)
    return surviving


def run_assertions(
    specs: list[AssertionSpec],
    ctx: AssertionContext,
) -> list[AssertionResult]:
    """Run every assertion (no short-circuit) and return their results."""
    out: list[AssertionResult] = []
    for spec in specs:
        start = time.perf_counter_ns()
        try:
            handler = _HANDLERS[spec.kind]
        except KeyError:
            out.append(AssertionResult(
                id=spec.id, kind=spec.kind,
                status="error", blocking=spec.blocking,
                message=spec.message,
                detail={"error_class": "ValueError",
                        "error_message": f"unknown kind: {spec.kind}"},
                duration_ms=0,
            ))
            continue
        try:
            status, detail = handler(spec, ctx)
            out.append(AssertionResult(
                id=spec.id, kind=spec.kind,
                status=status, blocking=spec.blocking,
                message=spec.message,
                detail=detail,
                duration_ms=int((time.perf_counter_ns() - start) / 1_000_000),
            ))
        except Exception as e:
            out.append(AssertionResult(
                id=spec.id, kind=spec.kind,
                status="error", blocking=spec.blocking,
                message=spec.message,
                detail={"error_class": e.__class__.__name__,
                        "error_message": str(e)[:500]},
                duration_ms=int((time.perf_counter_ns() - start) / 1_000_000),
            ))
    return out


def summarize(results: list[AssertionResult]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errors = sum(1 for r in results if r.status == "error")
    blocking_failed = sum(
        1 for r in results
        if r.blocking and r.status != "pass"
    )
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "blocking_failed": blocking_failed,
        "all_blocking_passed": blocking_failed == 0,
    }
