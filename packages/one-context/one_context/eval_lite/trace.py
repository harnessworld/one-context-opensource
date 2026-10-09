"""trace — claude -p stream-json 事件流解析（C-04 已验通）。

probe 档只验 skill 触发 y/n，不判产出。本模块负责：
  1. spawn `claude -p --output-format stream-json --verbose` 跑一条 prompt；
  2. 从 NDJSON 事件流抠 Skill tool_use，判目标 skill 是否被调用（归一化匹配）；
  3. 从末行 result 事件收 cost/duration/num_turns；cc_version 单采（不在流里）。

C-04 spike 实锚（2026-06-28）：
  - Skill 触发 = assistant message 的 content[] 里 tool_use{name:"Skill",input.skill}
  - input.skill 带 plugin 命名空间（如 ponytail:ponytail-help）→ 匹配需归一化
  - result 末行含 total_cost_usd / duration_ms / num_turns / session_id / usage
  - cc_version 不在 stream-json 信封 → spawn 外单独 `claude --version` 采
  - 触发-only 用 --max-turns N 截断控本（Skill tool_use 在前期就出，截断不影响 y/n）
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class NoAuthError(RuntimeError):
    """无 ANTHROPIC_AUTH_TOKEN/API_KEY → probe 整体降级 INSUFFICIENT_EVIDENCE（D8）。"""


class CcSpawnError(RuntimeError):
    """claude -p 非 0 退出或超时。"""


@dataclass
class TriggerEvidence:
    """一条 probe prompt 的触发判定证据。"""

    category: str                       # explicit/implicit/context/negative
    prompt: str
    triggered: bool                     # 目标 skill 是否被调用
    matched_raw: list[str] = field(default_factory=list)  # 命中的 input.skill 原值
    all_skill_calls: list[str] = field(default_factory=list)  # 全部 Skill 调用（debug 用）
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    error: str = ""                     # spawn 失败原因（非空 → triggered 置 False，上浮判定）
    transcript_path: str = ""           # 落盘 transcript（可复现 D9.2）


def _skill_match(raw: str, target: str) -> bool:
    """input.skill（可能带 plugin 命名空间）与目标 skill 名匹配。

    ponytail: 严格 basename 相等优先；包含作兜底（trigger 偶用别名/缩写）。
    已知 ceiling：包含匹配可能误触（如 `remotion` 命中 `remotion-pipelines` 反向），
    触发面收窄后再收紧为严格相等。
    """
    if not raw:
        return False
    basename = raw.split(":")[-1].strip()
    if basename == target:
        return True
    return basename in target or target in basename


def parse_skill_trigger(ndjson_text: str, target_skill: str) -> dict[str, Any]:
    """纯解析：NDJSON 文本 → {matched, all_calls, cost, duration, num_turns}。

    不 spawn，零依赖，可单测。spawn 由 spawn_cc_probe 负责。
    """
    matched: list[str] = []
    all_calls: list[str] = []
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    result_block: dict[str, Any] | None = None

    for line in ndjson_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        # Skill tool_use 在 assistant message 的 content[]
        msg = d.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "Skill":
                    raw = ""
                    inp = b.get("input")
                    if isinstance(inp, dict):
                        raw = str(inp.get("skill", "") or "")
                    all_calls.append(raw)
                    if _skill_match(raw, target_skill):
                        matched.append(raw)
        # 末行 result 事件（subtype=success/error 都带汇总字段）
        if d.get("type") == "result":
            result_block = d

    if result_block:
        cost_usd = result_block.get("total_cost_usd")
        duration_ms = result_block.get("duration_ms")
        num_turns = result_block.get("num_turns")

    return {
        "matched": matched,
        "all_calls": all_calls,
        "triggered": len(matched) > 0,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "result_subtype": (result_block or {}).get("subtype", ""),
    }


def collect_cc_version() -> str:
    """cc_version 不在 stream-json 信封里（C-04 实锚），单独采。best-effort。"""
    try:
        r = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def spawn_cc_probe(
    prompt: str,
    *,
    workdir: Path,
    max_turns: int = 2,
    timeout: int = 180,
) -> tuple[str, str]:
    """spawn claude -p stream-json 跑一条 prompt，返回 (stdout NDJSON, stderr)。

    无 key 抛 NoAuthError（调用方降级 INSUFFICIENT_EVIDENCE）。
    max_turns 截断控本（默认2：Skill 触发在前期就出，截断不影响 y/n，D8）。
    """
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not token:
        raise NoAuthError("无 ANTHROPIC_AUTH_TOKEN/API_KEY")
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if max_turns and max_turns > 0:
        cmd += ["--max-turns", str(max_turns)]
    try:
        r = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise CcSpawnError(f"claude -p 超时（{timeout}s）") from e
    # cc 即使 -p 成功也可能 stderr 有 warning；stdout 拿到 NDJSON 即视为可解析
    return r.stdout, r.stderr
