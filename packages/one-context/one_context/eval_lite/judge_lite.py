"""judge_lite — full 档 LLM 裁判（D9 原则4 + D9.2 锁 JSON rubric）。

D9 原则4：规则不够时再让模型补。judge 只接规则覆盖不了的（过程/风格主观维度），
不可反过来先 judge（D9.4 full 顺序 quick→probe→judge）。
D9.2：judge rubric 必须可执行判定，锁 JSON schema
      {dimension, score(0-1), evidence, rationale, verdict}，
      模糊描述 rubric 拒绝加载。

spawn claude -p + 强制 JSON 输出。judge 失败（无合法 JSON）→ 维度 INSUFFICIENT_EVIDENCE。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


JUDGE_SCHEMA_KEYS = {"dimension", "score", "evidence", "rationale", "verdict"}
VALID_VERDICTS = {"PASS", "FAIL"}


class JudgeError(ValueError):
    pass


@dataclass
class JudgeResult:
    """judge 对一个主观维度的判定。"""

    dimension: str
    score: float           # 0..1
    evidence: str          # trace/产物里支撑判定的具体证据
    rationale: str         # 为什么这个分/verdict
    verdict: str           # PASS / FAIL（score >= threshold ? PASS : FAIL，由调用方定阈）
    error: str = ""        # spawn/解析失败原因


def _judge_prompt(dimension: str, rubric: str, context_text: str) -> str:
    return f"""你是 Skill 评测裁判。只评【{dimension}】维度。

判定标准（必须可执行，模糊则判 FAIL）：
{rubric}

证据上下文（cc 跑 skill 的 trace 摘要 + 产物）：
---
{context_text}
---

严格只输出一个 JSON 对象，不要 markdown 代码块、不要解释：
{{"dimension": "{dimension}", "score": <0到1的浮点>, "evidence": "<trace/产物里具体证据>",
  "rationale": "<为什么这个分>", "verdict": "PASS或FAIL"}}

score 映射：完全合规=1.0，轻微偏差=0.7-0.9，明显违规<0.6。verdict 由你是否认为该维度合格决定。"""


def judge_dimension(
    dimension: str,
    rubric: str,
    context_text: str,
    *,
    env: dict[str, str],
    workdir: str | None = None,
    timeout: int = 120,
    model: str | None = None,
) -> JudgeResult:
    """spawn claude -p 评一个主观维度。锁 JSON schema（parse 失败返回 error）。

    rubric 空或不可执行 → JudgeError（调用方应在加载时拒绝，D9.2）。
    """
    if not rubric or not rubric.strip():
        raise JudgeError(f"维度 {dimension} 缺 judge_rubric（D9.2：模糊描述拒绝加载）")
    if len(rubric.strip()) < 10:
        raise JudgeError(f"维度 {dimension} judge_rubric 过短，不可执行判定（D9.2）")

    prompt = _judge_prompt(dimension, rubric, context_text)
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", "1"]
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, cwd=workdir, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error="judge 超时")
    if r.returncode != 0 and not r.stdout:
        return JudgeResult(dimension, 0.0, "", "", "FAIL",
                           error=f"claude exit={r.returncode}: {r.stderr[:200]}")

    # 从 stream-json 末行 result.result 抠 JSON 文本
    result_text = ""
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "result":
            result_text = d.get("result", "") or ""
    return _parse_judge_json(dimension, result_text)


def _parse_judge_json(dimension: str, text: str) -> JudgeResult:
    """从 cc 输出文本抠 JSON 对象，锁 schema。容错剥离 markdown 围栏。"""
    if not text:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error="judge 无输出")
    # 剥 ```json ... ``` 围栏（cc 偶尔不听话加围栏）
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    # 抠第一个 {...} 块
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error=f"judge 输出非 JSON: {stripped[:120]}")
    try:
        obj = json.loads(stripped[start:end + 1])
    except Exception as e:
        return JudgeResult(dimension, 0.0, "", "", "FAIL", error=f"judge JSON 解析失败: {e}")

    missing = JUDGE_SCHEMA_KEYS - set(obj.keys())
    if missing:
        return JudgeResult(dimension, 0.0, "", "", "FAIL",
                           error=f"judge JSON 缺字段 {missing}")
    verdict = str(obj["verdict"]).strip().upper()
    if verdict not in VALID_VERDICTS:
        return JudgeResult(dimension, 0.0, "", "", "FAIL",
                           error=f"judge verdict 非法: {verdict}")
    try:
        score = float(obj["score"])
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    return JudgeResult(
        dimension=dimension,
        score=score,
        evidence=str(obj.get("evidence", "")),
        rationale=str(obj.get("rationale", "")),
        verdict=verdict,
    )


def judge_verdict_from_score(score: float, threshold: float | None) -> str:
    """score → verdict：score >= threshold ? PASS : FAIL。threshold 缺省 0.8。"""
    th = threshold if threshold is not None else 0.8
    return "PASS" if score >= th else "FAIL"
