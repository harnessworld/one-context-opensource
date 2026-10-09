"""report — 维度矩阵 + 版本曲线。

两版：
- render_report (markdown)：文字摘要，各 run verdict + 维度 + 退化明细。
- render_html_report (HTML, D10)：带档 verdict 红绿带 + commit 横轴曲线 +
  三字形 quick/probe/full 虚实线 + 系统态/警告旗二级徽章。

HTML 用纯 f-string + 内联 CSS + 原生 <details> 折叠（零 JS、零依赖）。
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
    # 按时间正序（runId 时间戳前缀）
    out.sort(key=lambda r: r.get("run_id", ""))
    return out


def render_report(skill: str, case: str, *, skills_root: Path) -> Path:
    case_dir = skills_root / skill / "evals" / case
    if not case_dir.is_dir():
        raise FileNotFoundError(f"case 不存在：{case_dir}")

    runs = _collect_runs(case_dir)
    out_path = case_dir / "report.md"

    lines: list[str] = []
    lines.append(f"# {skill}/{case} 评测报告\n")
    if not runs:
        lines.append("（暂无 run 记录。先 `onecxt eval run <skill>/<case>` 跑一次。）\n")
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    # D6③ 顶层 skill@sha 结论（最新 run）
    latest = runs[-1]
    lines.append(f"**最新 verdict**：`{latest['verdict']['verdict']}` "
                 f"@ `{latest.get('git_sha','')[:8]}` mode=`{latest.get('mode')}`\n")
    if latest["verdict"].get("rationale"):
        lines.append(f"> {latest['verdict']['rationale']}\n")

    # 维度矩阵（最新 run）
    lines.append("## 维度矩阵（最新）\n")
    lines.append("| 维度 | role | verdict | 明细 |")
    lines.append("|---|---|---|---|")
    for d in latest["verdict"].get("dimensions", []):
        lines.append(f"| {d['dimension']} | {d['role']} | {d['verdict']} | {d.get('summary','')} |")
    lines.append("")

    # 版本曲线（runId 折叠，D10 横轴=commit，首版文字时间轴）
    lines.append("## 版本历史\n")
    lines.append("| run_id | mode | git_sha | verdict | rationale |")
    lines.append("|---|---|---|---|---|")
    for r in runs:
        sha = (r.get("git_sha") or "")[:8] or "(缺)"
        lines.append(f"| {r.get('run_id','')} | {r.get('mode','')} | `{sha}` | "
                     f"{r['verdict']['verdict']} | {r['verdict'].get('rationale','')} |")
    lines.append("")

    # 警告旗（D8/D9.5 二级徽章，首版文字）
    warn_runs = [r for r in runs if r.get("warnings")]
    if warn_runs:
        lines.append("## 警告旗\n")
        for r in warn_runs:
            lines.append(f"- `{r.get('run_id','')}`: {', '.join(r['warnings'])}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# --- D10 HTML 带档曲线 ---

_MODE_RANK = {"full": 3, "probe": 2, "quick": 1}
_MODE_GLYPH = {"quick": "●", "probe": "△", "full": "◆"}
_VERDICT_COLOR = {
    "PASS": "#2d7d46",
    "FAIL": "#c0392b",
    "INSUFFICIENT_EVIDENCE": "#8a8f98",
}
_WINDOW = 20  # D10：横轴窗口最近 ~20 commit


def _collect_commits(repo_root: Path, n: int = _WINDOW) -> list[dict[str, str]]:
    """git log 拿最近 n commit：[{sha, short, msg}]，新→旧。best-effort。"""
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
    """run 列表 × commit 窗口 → 每 commit 的带档判定（D6③ + D10）。

    每 commit：跑过的档取最高档(full>probe>quick) run 的 verdict 作带档 verdict；
    未跑 → None（未评测，灰）。三字形标记哪些档跑过。
    """
    by_sha: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        sha = run.get("git_sha", "")
        if sha:
            by_sha.setdefault(sha, []).append(run)

    curve = []
    for c in commits:
        sha_runs = by_sha.get(c["sha"], [])
        # 同 sha 多 run 每 mode 取最新（run_id 时间戳排序）
        latest_per_mode: dict[str, dict[str, Any]] = {}
        for run in sorted(sha_runs, key=lambda r: r.get("run_id", "")):
            latest_per_mode[run.get("mode", "quick")] = run
        modes_run = sorted(latest_per_mode.keys(), key=lambda m: _MODE_RANK.get(m, 0))
        if not modes_run:
            curve.append({**c, "modes": [], "best_mode": None,
                          "verdict": None, "has_full": False})
            continue
        best_mode = modes_run[-1]  # 最高档
        best_run = latest_per_mode[best_mode]
        curve.append({
            **c, "modes": modes_run, "best_mode": best_mode,
            "verdict": best_run.get("verdict", {}).get("verdict"),
            "has_full": "full" in modes_run,
            "best_rationale": best_run.get("verdict", {}).get("rationale", ""),
        })
    return curve


def _rollup_verdict(runs: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """D6③ skill@sha 顶层：取最新 run 的 verdict + best_mode（首屏发车信号）。"""
    if not runs:
        return None, None
    latest = max(runs, key=lambda r: r.get("run_id", ""))
    v = latest.get("verdict", {}).get("verdict")
    m = latest.get("mode")
    return v, m


def render_html_report(skill: str, case: str, *, skills_root: Path) -> Path:
    """D10 HTML 报告：带档 verdict 首屏 + commit 红绿带 + 三字形虚实线。"""
    case_dir = skills_root / skill / "evals" / case
    if not case_dir.is_dir():
        raise FileNotFoundError(f"case 不存在：{case_dir}")
    runs = _collect_runs(case_dir)
    repo_root = skills_root.resolve().parent
    commits = _collect_commits(repo_root)
    curve = _aggregate_curve(runs, commits)

    top_v, top_m = _rollup_verdict(runs)
    banded = f"{top_v}@{top_m}" if top_v and top_m and top_v == "PASS" else (top_v or "未评测")

    # 系统态 + 警告旗二级徽章计数
    sys_count = sum(1 for r in runs if r.get("verdict", {}).get("verdict") == "INSUFFICIENT_EVIDENCE")
    warn_items: list[tuple[str, str]] = []
    for r in runs:
        for w in r.get("warnings", []) or []:
            warn_items.append((r.get("run_id", "")[-6:], w))

    def _cell(c: dict[str, Any]) -> str:
        v = c.get("verdict")
        color = _VERDICT_COLOR.get(v, "#d5dbe1") if v else "#eef1f4"  # 未评测 浅灰
        glyphs = "".join(
            f'<span class="glyph {m}{"" if c.get("has_full") or m=="full" else " soft"}">{_MODE_GLYPH[m]}</span>'
            for m in c.get("modes", [])
        )
        border = "solid" if c.get("has_full") else "dashed"  # quick/probe-only 段虚线
        title = f"{c['short']} {c['msg']} | modes={c.get('modes')} verdict={v}"
        return (f'<td class="cell {border}" style="background:{color}" title="{html.escape(title)}">'
                f'<div class="glyphs">{glyphs}</div></td>')

    cells = "".join(_cell(c) for c in curve)
    labels = "".join(
        f'<td class="label" title="{html.escape(c["msg"]) }">{html.escape(c["short"])}</td>'
        for c in curve
    )

    # 失败/系统态 commit 维度明细（折叠）
    detail_rows = ""
    detail_runs = [r for r in runs if r.get("verdict", {}).get("verdict") in ("FAIL", "INSUFFICIENT_EVIDENCE")]
    if detail_runs:
        for r in detail_runs:
            sha = (r.get("git_sha") or "")[:8] or "(缺)"
            dims = " · ".join(
                f"{d['dimension']}={d['verdict']}"
                for d in r.get("verdict", {}).get("dimensions", [])
            ) or "—"
            detail_rows += (f"<tr><td>{r.get('mode','')}</td><td><code>{sha}</code></td>"
                            f"<td>{r.get('verdict',{}).get('verdict','')}</td>"
                            f"<td>{html.escape(r.get('verdict',{}).get('rationale','') or dims)}</td></tr>\n")

    badge_color = _VERDICT_COLOR.get(top_v, "#8a8f98")
    page = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>{html.escape(skill)}/{html.escape(case)} 评测</title>
<style>
  body {{ font: 14px/1.5 -apple-system, "PingFang SC", sans-serif; margin: 24px; color: #1f2328; }}
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
<div class="badges">系统态 {sys_count} · 警告 {len(warn_items)}
{(''.join(f'<br>· {rid}: {html.escape(w)}' for rid,w in warn_items) if warn_items else '')}</div>

<table class="curve"><tr>{cells}</tr>
<tr>{labels}</tr></table>

<div class="legend">
  <span class="glyph quick">●</span>quick <span class="glyph probe">△</span>probe <span class="glyph full">◆</span>full
  · 虚线框=仅规格/触发覆盖（未 full 验）· 实线框=有 full 点
  · 绿=PASS 红=FAIL 灰=INSUFFICIENT 浅灰=未评测
</div>

{f'<details open><summary>失败/系统态 run 维度明细（{len(detail_runs)}）</summary><table class="detail"><tr><th>mode</th><th>sha</th><th>verdict</th><th>rationale</th></tr>{detail_rows}</table></details>' if detail_runs else ''}

<details><summary>全部 run 历史（{len(runs)}）</summary>
<table class="detail"><tr><th>run_id</th><th>mode</th><th>sha</th><th>verdict</th></tr>
{"".join(f'<tr><td>{r.get("run_id","")}</td><td>{r.get("mode","")}</td><td><code>{(r.get("git_sha","")[:8]) or "(缺)"}</code></td><td>{r.get("verdict",{}).get("verdict","")}</td></tr>' for r in runs)}
</table></details>
</body></html>"""

    out_path = case_dir / "report.html"
    out_path.write_text(page, encoding="utf-8")
    return out_path
