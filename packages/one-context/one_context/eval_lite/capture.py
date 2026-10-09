"""capture — one-command case creation: satisfactory artifact → case baseline.

The measured values of a satisfactory artifact become the standard: the
artifact's assertions are executed once, passing ones enter the baseline
expectations, failing/erroring ones are listed for review (surfacing gaps IS
the job of capture). No agent CLI is involved (pure local structural
inference), which is what makes it one-command.

Usage (CLI wrapper):
  onecxt eval capture --skill <name> --from <artifact dir> --prompt "<the explicit invocation prompt>"

Outputs:
  skills/<name>/evals/<case>/baseline.json   # expectation snapshot of passing assertions
  skills/<name>/evals/<case>/scenario.yaml   # case configuration
  skills/<name>/evals/<case>/prompts/explicit/01.md
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from one_context.eval.assertions import (
    AssertionContext,
    AssertionSpec,
    run_assertions,
)
from one_context.eval_lite.dimensions import load_dimensions
from one_context.eval_lite.schema import (
    Baseline,
    BaselineExpectation,
    CaseScenario,
    PromptSpec,
)


class CaptureError(ValueError):
    pass


@dataclass
class CaptureReport:
    """Summary of a capture run (printed by the CLI, useful for review)."""

    case_dir: Path
    passed: int
    failed: int
    errored: int
    # Assertion ids + reasons for fail/error (artifact gaps; decide whether
    # to add the missing artifact or drop the assertion).
    gaps: list[dict[str, Any]]
    baseline_path: Path
    scenario_path: Path
    prompt_path: Path
    # capture only records one explicit prompt; zero negative controls means
    # the trigger dimension will be INSUFFICIENT_EVIDENCE at run time.
    needs_negatives: bool = True


def _flatten_assertions(dims: dict[str, dict[str, Any]]) -> list[tuple[str, AssertionSpec]]:
    """Collect (dimension key, assertion) pairs from the dimension template.

    Dimension ownership follows the yaml grouping; the kind-based default
    mapping in assertion_map is only a fallback.
    """
    out: list[tuple[str, AssertionSpec]] = []
    for dim_key, spec in dims.items():
        for a in spec.get("assertions", []) or []:
            d = dict(a)
            d.setdefault("id", f"{dim_key}_{len(out)}")
            d.setdefault("kind", "file_exists")
            d.setdefault("blocking", True)
            out.append((dim_key, AssertionSpec(**{k: v for k, v in d.items()})))
    return out


def _expected_value(spec: AssertionSpec, result_detail: dict[str, Any]) -> Any:
    """Derive the expectation (measured value) from an assertion detail.

    file_exists → True; json_field exists → True; numeric kinds → measured
    value. Simplified: file-kind assertions store True ("exists is
    expected"); the rest store the spec's own parameters (they passed at
    capture time).
    """
    if spec.kind in ("file_exists",):
        return True
    if spec.kind == "file_absent" or spec.kind == "path_absent":
        return True
    if spec.kind == "text_contains" or spec.kind == "text_absent":
        return spec.needle
    if spec.kind == "json_field":
        return result_detail.get("actual", spec.value)
    return True


def capture_case(
    *,
    skill: str,
    from_dir: Path,
    prompt: str,
    case: str | None = None,
    dims_path: Path | None = None,
) -> CaptureReport:
    """Freeze a satisfactory artifact into a case baseline.

    from_dir: the artifact root (used as the assertion path base).
    case: defaults to the from_dir directory name.
    dims_path: defaults to skills/<skill>/eval.dimensions.yaml.
    """
    from_dir = from_dir.resolve()
    if not from_dir.is_dir():
        raise CaptureError(f"--from directory does not exist: {from_dir}")
    if not prompt.strip():
        raise CaptureError("--prompt must not be empty (the first explicit invocation sample)")

    case = case or from_dir.name
    if dims_path is None:
        # Walk up from from_dir to the workspace root (has meta/repos.yaml),
        # then resolve skills/<skill>/eval.dimensions.yaml.
        root = _repo_root(from_dir)
        dims_path = root / "skills" / skill / "eval.dimensions.yaml"
    dims = load_dimensions(dims_path)

    assertions = _flatten_assertions(dims)
    ctx = AssertionContext(
        sandbox_root=from_dir,
        artifacts_base=from_dir,   # path base = artifact root
        final_text="",
        stdout="",
        stderr_tail="",
        tool_calls=[],
        produced_paths=set(),
    )

    specs_only = [s for _, s in assertions]
    results = run_assertions(specs_only, ctx)

    passed, failed, errored = 0, 0, 0
    expectations: list[BaselineExpectation] = []
    gaps: list[dict[str, Any]] = []
    for (dim_key, spec), res in zip(assertions, results):
        # Dimension ownership follows the yaml grouping; role from blocking (blocking→gate).
        if res.status == "pass":
            passed += 1
            expectations.append(BaselineExpectation(
                assertion_id=spec.id,
                dimension=dim_key,  # type: ignore[arg-type]
                role="gate" if spec.blocking else "score",
                expected=_expected_value(spec, res.detail),
            ))
        else:
            failed += 1 if res.status == "fail" else 0
            errored += 1 if res.status == "error" else 0
            gaps.append({
                "id": spec.id,
                "kind": spec.kind,
                "dimension": dim_key,
                "status": res.status,
                "detail": res.detail,
                "hint": "artifact is missing this file/field: add it and re-capture, "
                        "or drop this assertion from the skill's eval.dimensions.yaml",
            })

    # Write the case directory relative to the workspace root (never cwd).
    root = _repo_root(from_dir)
    case_dir = root / "skills" / skill / "evals" / case
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "prompts" / "explicit").mkdir(parents=True, exist_ok=True)

    git_sha = _git_sha(from_dir)
    baseline = Baseline(
        skill=skill,
        case=case,
        captured_at=str(int(time.time())),
        captured_from=str(from_dir),
        git_sha=git_sha,
        expectations=expectations,
    )
    baseline_path = case_dir / "baseline.json"
    baseline_path.write_text(
        baseline.model_dump_json(indent=2), encoding="utf-8"
    )
    import json
    # Verify the baseline parses as valid JSON (model_dump_json already is).
    _ = json.loads(baseline_path.read_text())

    scenario = CaseScenario(
        skill=skill,
        case=case,
        target_path=str(from_dir),
        dimension_config=str(dims_path),
        prompts=[PromptSpec(category="explicit", prompt=prompt)],
    )
    scenario_path = case_dir / "scenario.yaml"
    scenario_path.write_text(
        yaml.safe_dump(scenario.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    prompt_path = case_dir / "prompts" / "explicit" / "01.md"
    prompt_path.write_text(prompt.strip() + "\n", encoding="utf-8")

    return CaptureReport(
        case_dir=case_dir,
        passed=passed,
        failed=failed,
        errored=errored,
        gaps=gaps,
        baseline_path=baseline_path,
        scenario_path=scenario_path,
        prompt_path=prompt_path,
        needs_negatives=True,  # capture only records the explicit prompt; negatives are added by hand
    )


def _git_sha(repo: Path) -> str:
    """Best-effort HEAD sha of the repository containing from_dir (reproducibility)."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _repo_root(start: Path) -> Path:
    """Walk up from start looking for the workspace root (has meta/repos.yaml);
    fall back to start itself."""
    p = start.resolve()
    for _ in range(10):
        if (p / "meta" / "repos.yaml").is_file():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start.resolve()