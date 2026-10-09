"""report — dimension matrix + version curve.

Two renderers:
- render_report (markdown): text summary — per-run verdicts, dimensions,
  and regression details.
- render_html_report (HTML): mode-qualified verdict band + commit-axis
  curve + quick/probe/full glyphs + system-state/warning badges.

The HTML renderer is pure f-strings + inline CSS + native <details>
folding (zero JavaScript, zero dependencies).
"""
from __future__ import annotations

import html
import json
import subprocess
from pathlib import Path
from typing import Any


def _collect_runs(case_dir: Path) -> list[dict[str, Any]]:
    runs_dir = case_dir / "__runs"
    if not runs_dir.is_dir():
        return []
    out = []
    for f in sorted(runs_dir.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    # Chronological order (run id has a timestamp prefix).
    out.sort(key=lambda r: r.get("run_id", ""))
    return out


def render_report(skill: str, case: str, *, skills_root: Path) -> Path:
    case_dir = skills_root / skill / "evals" / case
    if not case_dir.is_dir():
        raise FileNotFoundError(f"case does not exist: {case_dir}")

    runs = _collect_runs(case_dir)
    out_path = case_dir / "report.md"

    lines: list[str] = []
    lines.append(f"# {skill}/{case} evaluation report\n")
    if not runs:
        lines.append("No runs yet. Run `onecxt eval run <skill>/<case>` first.\n")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    # Top-level skill@sha conclusion (latest run).
    latest = runs[-1]
    lines.append(f"**Latest verdict**: `{latest['verdict']['verdict']}` "
                 f"@ `{latest.get('git_sha','')[:8]}` mode=`{latest.get('mode')}`\n")
    if latest["verdict"].get("rationale"):
        lines.append(f"> {latest['verdict']['rationale']}\n")

    # Dimension matrix (latest run).
    lines.append("## Dimension matrix (latest)\n")
    lines.append("| dimension | role | verdict | detail |")
    lines.append("|---|---|---|---|")
    for d in latest["verdict"].get("dimensions", []):
        lines.append(f"| {d['dimension']} | {d['role']} | {d['verdict']} | {d.get('summary','')} |")
    lines.append("")

    # Version history (run ids collapsible; commit axis, text timeline).
    lines.append("## Version history\n")
    lines.append("| run_id | mode | git_sha | verdict | rationale |")
    lines.append("|---|---|---|---|---|")
    for r in runs:
        sha = (r.get("git_sha") or "")[:8] or "(missing)"
        lines.append(f"| {r.get('run_id','')} | {r.get('mode','')} | `{sha}` | "
                     f"{r['verdict']['verdict']} | {r['verdict'].get('rationale','')} |")
    lines.append("")

    # Warning badges.
    warn_runs = [r for r in runs if r.get("warnings")]
    if warn_runs:
        lines.append("## Warnings\n")
        for r in warn_runs:
            lines.append(f"- `{r.get('run_id','')}`: {', '.join(r['warnings'])}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# --- HTML mode-qualified curve ---

_MODE_RANK = {"full": 3, "probe": 2, "quick": 1}
_MODE_GLYPH = {"quick": "●", "probe": "△", "full": "◆"}
_VERDICT_COLOR = {
    "PASS": "#2d7d46",
    "FAIL": "#c0392b",
    "INSUFFICIENT_EVIDENCE": "#8a8f98",
}
_WINDOW = 20  # horizontal axis window: roughly the last 20 commits


def _collect_commits(repo_root: Path, n: int = _WINDOW) -> list[dict[str, str]]:
    """git log for the last n commits: [{sha, short, msg}], newest first. Best-effort."""
    try:
        r = subprocess.run(
            ["git", "log", f"-{n}", "--pretty=format:%H|%s"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        if "|" not in line:
            continue
        sha, _, msg = line.partition("|")
        out.append({"sha": sha, "short": sha[:8], "msg": msg.strip()})
    return out


def _aggregate_curve(runs: list[dict[str, Any]],
                     commits: list[dict[str, str]]) -> list[dict[str, Any]]:
    """runs × commit window → one mode-qualified verdict per commit.

    Per commit: among the modes that ran, the highest-mode (full > probe >
    quick) latest run provides the mode-qualified verdict; commits with no
    runs get None (not evaluated, grey). The glyphs mark which modes ran.
    """
    by_sha: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        sha = run.get("git_sha", "")
        if sha:
            by_sha.setdefault(sha, []).append(run)

    curve = []
    for c in commits:
        sha_runs = by_sha.get(c["sha"], [])
        # Multiple runs on the same sha: keep the latest run per mode (run-id order).
        latest_per_mode: dict[str, dict[str, Any]] = {}
        for run in sorted(sha_runs, key=lambda r: r.get("run_id", "")):
            latest_per_mode[run.get("mode", "quick")] = run
        modes_run = sorted(latest_per_mode.keys(), key=lambda m: _MODE_RANK.get(m, 0))
        if not modes_run:
            curve.append({**c, "modes": [], "best_mode": None,
                          "verdict": None, "has_full": False})
            continue
        best_mode = modes_run[-1]  # highest mode
        best_run = latest_per_mode[best_mode]
        curve.append({
            **c, "modes": modes_run, "best_mode": best_mode,
            "verdict": best_run.get("verdict", {}).get("verdict"),
            "has_full": "full" in modes_run,
            "best_rationale": best_run.get("verdict", {}).get("rationale", ""),
        })
    return curve


def _rollup_verdict(runs: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Top-level skill@sha rollup: latest run's verdict + best mode (the
    first-screen ship signal)."""
    if not runs:
        return None, None
    latest = max(runs, key=lambda r: r.get("run_id", ""))
    v = latest.get("verdict", {}).get("verdict")
    m = latest.get("mode")
    return v, m


def render_html_report(skill: str, case: str, *, skills_root: Path) -> Path:
    """HTML report: mode-qualified verdict front page + commit red/green band
    + mode glyphs."""
    case_dir = skills_root / skill / "evals" / case
    if not case_dir.is_dir():
        raise FileNotFoundError(f"case does not exist: {case_dir}")
    runs = _collect_runs(case_dir)
    repo_root = skills_root.resolve().parent
    commits = _collect_commits(repo_root)
    curve = _aggregate_curve(runs, commits)

    top_v, top_m = _rollup_verdict(runs)
    banded = f"{top_v}@{top_m}" if top_v and top_m and top_v == "PASS" else (top_v or "not evaluated")

    # System-state + warning badge counts.
    sys_count = sum(1 for r in runs if r.get("verdict", {}).get("verdict") == "INSUFFICIENT_EVIDENCE")
    warn_items: list[tuple[str, str]] = []
    for r in runs:
        for w in r.get("warnings", []) or []:
            warn_items.append((r.get("run_id", "")[-6:], w))

    def _cell(c: dict[str, Any]) -> str:
        v = c.get("verdict")
        color = _VERDICT_COLOR.get(v, "#d5dbe1") if v else "#eef1f4"  # not evaluated: light grey
        glyphs = "".join(
            f'<span class="glyph {m}{"" if c.get("has_full") or m=="full" else " soft"}">{_MODE_GLYPH[m]}</span>'
            for m in c.get("modes", [])
        )
        border = "solid" if c.get("has_full") else "dashed"  # quick/probe-only segments are dashed
        title = f"{c['short']} {c['msg']} | modes={c.get('modes')} verdict={v}"
        return (f'<td class="cell {border}" style="background:{color}" title="{html.escape(title)}">'
                f'<div class="glyphs">{glyphs}</div></td>')

    cells = "".join(_cell(c) for c in curve)
    labels = "".join(
        f'<td class="label" title="{html.escape(c["msg"]) }">{html.escape(c["short"])}</td>'
        for c in curve
    )

    # Failed/system-state runs: per-dimension details (collapsible).
    detail_rows = ""
    detail_runs = [r for r in runs if r.get("verdict", {}).get("verdict") in ("FAIL", "INSUFFICIENT_EVIDENCE")]
    if detail_runs:
        for r in detail_runs:
            sha = (r.get("git_sha") or "")[:8] or "(missing)"
            dims = " · ".join(
                f"{d['dimension']}={d['verdict']}"
                for d in r.get("verdict", {}).get("dimensions", [])
            ) or "—"
            detail_rows += (f"<tr><td>{r.get('mode','')}</td><td><code>{sha}</code></td>"
                            f"<td>{r.get('verdict',{}).get('verdict','')}</td>"
                            f"<td>{html.escape(r.get('verdict',{}).get('rationale','') or dims)}</td></tr>\n")

    badge_color = _VERDICT_COLOR.get(top_v, "#8a8f98")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(skill)}/{html.escape(case)} evaluation</title>
<style>
  body {{ font: 14px/1.5 -apple-system, sans-serif; margin: 24px; color: #1f2328; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .top {{ display:flex; align-items:baseline; gap:12px; margin-bottom:16px; }}
  .badge {{ padding:2px 10px; border-radius:4px; color:#fff; font-weight:600; font-size:15px; background:{badge_color}; }}
  .badges {{ margin:8px 0 20px; color:#6a737d; font-size:12px; }}
  table.curve {{ border-collapse:separate; border-spacing:3px 0; }}
  .cell {{ width:26px; height:34px; text-align:center; border-radius:3px; border-width:2px; border-color:#fff0; }}
  .cell.solid {{ border-color:#1f2328; }}
  .cell.dashed {{ border-color:#8a8f98; border-style:dashed; }}
  .glyphs {{ font-size:11px; line-height:1; color:#fff; opacity:.92; }}
  .glyph.soft {{ opacity:.45; }}
  .glyph.probe,.glyph.full {{ color:#fff; }}
  td.label {{ font-size:10px; color:#6a737d; text-align:center; font-family:ui-monospace,monospace; }}
  .legend {{ margin-top:20px; color:#6a737d; font-size:12px; }}
  details {{ margin-top:18px; }}
  table.detail {{ border-collapse:collapse; margin-top:8px; font-size:12px; }}
  table.detail td {{ border:1px solid #e1e4e8; padding:4px 8px; }}
  code {{ background:#f6f8fa; padding:1px 4px; border-radius:3px; }}
</style></head><body>
<h1>{html.escape(skill)} / {html.escape(case)}</h1>
<div class="top">skill@<code>{(commits[0]['short'] if commits else '?')}</code>
  <span class="badge">{html.escape(banded)}</span></div>
<div class="badges">system-state {sys_count} · warnings {len(warn_items)}
{(''.join(f'<br>· {rid}: {html.escape(w)}' for rid,w in warn_items) if warn_items else '')}</div>

<table class="curve"><tr>{cells}</tr>
<tr>{labels}</tr></table>

<div class="legend">
  <span class="glyph quick">●</span>quick <span class="glyph probe">△</span>probe <span class="glyph full">◆</span>full
  · dashed border = spec/trigger coverage only (no full run) · solid border = has a full run
  · green = PASS, red = FAIL, grey = INSUFFICIENT, light grey = not evaluated
</div>

{f'<details open><summary>Failed/system-state run details ({len(detail_runs)})</summary><table class="detail"><tr><th>mode</th><th>sha</th><th>verdict</th><th>rationale</th></tr>{detail_rows}</table></details>' if detail_runs else ''}

<details><summary>All run history ({len(runs)})</summary>
<table class="detail"><tr><th>run_id</th><th>mode</th><th>sha</th><th>verdict</th></tr>
{"".join(f'<tr><td>{r.get("run_id","")}</td><td>{r.get("mode","")}</td><td><code>{(r.get("git_sha","")[:8]) or "(missing)"}</code></td><td>{r.get("verdict",{}).get("verdict","")}</td></tr>' for r in runs)}
</table></details>
</body></html>"""

    out_path = case_dir / "report.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path