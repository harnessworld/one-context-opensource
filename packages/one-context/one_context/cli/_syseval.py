"""onecxt eval {capture|run|report} — Skill 系统化评测（eval_lite）。

feature: skill-systematic-eval
设计: features/core/skill-systematic-eval/tech_design.md
"""
from __future__ import annotations

import argparse
from pathlib import Path


def register(sub) -> None:
    p = sub.add_parser(
        "eval",
        help="Skill 系统化评测：capture 建案 / run 跑评测 / report 看曲线",
    )
    sp = p.add_subparsers(dest="eval_command", required=True)

    # capture
    pc = sp.add_parser("capture", help="一键建案：满意产物固化为 case baseline")
    pc.add_argument("--skill", required=True)
    pc.add_argument("--from", dest="from_dir", required=True, help="满意产物目录（feature 根）")
    pc.add_argument("--prompt", required=True, help="满意那轮的显式调用 prompt")
    pc.add_argument("--case", default=None, help="case 名（默认 = 目录名）")
    pc.set_defaults(func=_cmd_eval_capture)

    # run
    pr = sp.add_parser("run", help="跑评测（默认 quick；--mode probe 验触发）")
    pr.add_argument("target", help="<skill>/<case>")
    pr.add_argument("--mode", default="quick", choices=["quick", "probe", "full"],
                     help="quick(秒级断言 0 token)/probe(spawn cc 验触发)/full(worktree+judge)")
    pr.add_argument("--max-turns", type=int, default=None,
                     help="cc 截断 turn 数（probe 默认2，full 默认6）")
    pr.set_defaults(func=_cmd_eval_run)

    # report
    pre = sp.add_parser("report", help="看维度矩阵 + 版本曲线")
    pre.add_argument("target", help="<skill>/<case>")
    pre.add_argument("--format", default="markdown", choices=["markdown", "html"],
                     help="markdown(文字) 或 html(D10 带档曲线红绿带)")
    pre.set_defaults(func=_cmd_eval_report)

    # list
    pl = sp.add_parser("list", help="列出某 skill 的 case")
    pl.add_argument("skill", nargs="?", default=None)
    pl.set_defaults(func=_cmd_eval_list)

    p.set_defaults(_eval_default=True)


def _resolve_skills_root(root: Path) -> Path:
    return root / "skills"


def _cmd_eval_capture(root: Path, args: argparse.Namespace) -> int:
    from one_context.eval_lite.capture import capture_case
    r = capture_case(
        skill=args.skill,
        from_dir=Path(args.from_dir),
        prompt=args.prompt,
        case=args.case,
    )
    print(f"✓ capture: {args.skill}/{r.case_dir.name}")
    print(f"  passed={r.passed} failed={r.failed} errored={r.errored}")
    print(f"  baseline: {r.baseline_path}")
    if r.gaps:
        print(f"  ⚠ 产物缺口（未入 baseline，供 review 补产物或移除断言）：")
        for g in r.gaps:
            print(f"    - {g['id']} [{g.get('kind')}] dim={g.get('dimension')} "
                  f"status={g.get('status','')} : {str(g.get('detail',g.get('reason','')))[:80]}")
    if r.needs_negatives:
        print("  ⓘ 暂无负控 prompt（D8：trigger 维度需补 negative case，run 时强制 INSUFFICIENT）")
    return 0


def _cmd_eval_run(root: Path, args: argparse.Namespace) -> int:
    if "/" not in args.target:
        print("用法：onecxt eval run <skill>/<case>", flush=True)
        return 2
    skill, case = args.target.split("/", 1)

    try:
        if args.mode == "quick":
            from one_context.eval_lite.run import quick_run
            r = quick_run(skill, case, skills_root=_resolve_skills_root(root))
            _print_quick(skill, case, r)
        elif args.mode == "probe":
            from one_context.eval_lite.run import probe_run
            mt = args.max_turns if args.max_turns is not None else 2
            r = probe_run(skill, case, skills_root=_resolve_skills_root(root), max_turns=mt)
            _print_probe(skill, case, r)
        elif args.mode == "full":
            from one_context.eval_lite.run import full_run
            mt = args.max_turns if args.max_turns is not None else 6
            r = full_run(skill, case, skills_root=_resolve_skills_root(root), max_turns=mt)
            _print_full(skill, case, r)
        else:
            print(f"✗ {args.mode} 档未知", flush=True)
            return 2
    except Exception as e:
        print(f"✗ run 失败：{e}", flush=True)
        return 1
    return 0


def _print_quick(skill: str, case: str, r) -> None:
    rec = r.run_record
    print(f"✓ quick run: {skill}/{case}  [{rec.run_id}]")
    print(f"  verdict: {rec.verdict.verdict}")
    if rec.verdict.rationale:
        print(f"  rationale: {rec.verdict.rationale}")
    print(f"  git_sha: {rec.git_sha or '(缺 → INSUFFICIENT_EVIDENCE)'}")
    print("  dimensions:")
    for d in rec.verdict.dimensions:
        print(f"    {d.dimension:12} role={d.role:5} verdict={d.verdict:6} {d.summary}")
    if r.deltas:
        print("  退化明细:")
        for dt in r.deltas:
            print(f"    ✗ {dt['assertion_id']} dim={dt['dimension']} "
                  f"now={dt['current_status']}")


def _print_probe(skill: str, case: str, r) -> None:
    rec = r.run_record
    print(f"✓ probe run: {skill}/{case}  [{rec.run_id}]")
    print(f"  verdict: {rec.verdict.verdict}")
    if rec.verdict.rationale:
        print(f"  rationale: {rec.verdict.rationale}")
    print(f"  cc_version: {rec.cc_version or '(缺 → INSUFFICIENT_EVIDENCE)'}")
    print(f"  git_sha: {rec.git_sha or '(缺)'}")
    if rec.cost_usd is not None:
        print(f"  cost: ${rec.cost_usd:.4f}  duration: {rec.duration_ms}ms")
    if rec.warnings:
        print(f"  warnings: {', '.join(rec.warnings)}")
    print("  trigger 探针:")
    for tp in rec.trigger_probes:
        flag = "✓" if tp.get("triggered") else "✗"
        want = "未触发(负控期望)" if tp.get("category") == "negative" else "触发(正控期望)"
        hit = "命中" if tp.get("triggered") else "未命中"
        err = f"  err={tp['error']}" if tp.get("error") else ""
        print(f"    {flag} [{tp.get('category'):8}] {hit}{want}{err}")
        if tp.get("matched_raw"):
            print(f"         matched: {tp.get('matched_raw')}")


def _print_full(skill: str, case: str, r) -> None:
    rec = r.run_record
    print(f"✓ full run: {skill}/{case}  [{rec.run_id}]")
    print(f"  verdict: {rec.verdict.verdict}")
    if rec.verdict.rationale:
        print(f"  rationale: {rec.verdict.rationale}")
    print(f"  cc_version: {rec.cc_version or '(缺 → INSUFFICIENT_EVIDENCE)'}")
    print(f"  git_sha: {rec.git_sha or '(缺)'}")
    if rec.cost_usd is not None:
        print(f"  cost: ${rec.cost_usd:.4f}  duration: {rec.duration_ms}ms")
    if rec.warnings:
        print(f"  warnings: {', '.join(rec.warnings)}")
    print("  dimensions:")
    for d in rec.verdict.dimensions:
        extra = ""
        if d.score is not None:
            extra = f" score={d.score:.2f}"
        print(f"    {d.dimension:12} role={d.role:5} verdict={d.verdict:6}{extra} {d.summary}")


def _cmd_eval_report(root: Path, args: argparse.Namespace) -> int:
    if "/" not in args.target:
        print("用法：onecxt eval report <skill>/<case>", flush=True)
        return 2
    skill, case = args.target.split("/", 1)
    if args.format == "html":
        from one_context.eval_lite.report import render_html_report
        out = render_html_report(skill, case, skills_root=_resolve_skills_root(root))
    else:
        from one_context.eval_lite.report import render_report
        out = render_report(skill, case, skills_root=_resolve_skills_root(root))
    print(f"✓ report [{args.format}]: {out}")
    return 0


def _cmd_eval_list(root: Path, args: argparse.Namespace) -> int:
    sr = _resolve_skills_root(root)
    if args.skill:
        d = sr / args.skill / "evals"
        if not d.is_dir():
            print(f"无 case：{d}")
            return 0
        cases = sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "baseline.json").is_file())
        print(f"{args.skill}: {len(cases)} case(s)")
        for c in cases:
            print(f"  - {c}")
        return 0
    # 列所有 skill
    print("Skill cases:")
    if not sr.is_dir():
        return 0
    for sp in sorted(sr.iterdir()):
        evdir = sp / "evals"
        if not evdir.is_dir():
            continue
        cases = [p.name for p in evdir.iterdir() if p.is_dir() and (p / "baseline.json").is_file()]
        if cases:
            print(f"  {sp.name}: {', '.join(sorted(cases))}")
    return 0
