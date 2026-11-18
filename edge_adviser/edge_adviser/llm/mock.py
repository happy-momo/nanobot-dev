"""MockGateway — deterministic, LLM-free stand-in for LLMGateway.

Used by demo mode (``config.demo = true``) so the full HMI — QA, deep reports,
learning — works with zero model infrastructure: no nanobot, no Ollama, no API
keys. Content is a pure function of the injected context blocks (the same
deterministic snapshot facts the real gateway would see), so the demo exercises
the real pipeline: freshness gate → context build → gateway.run → archive/answer.

Deliberately duck-types LLMGateway.run so callers (ReportGenerator, QAEngine,
Learner) cannot tell the difference. A small artificial latency makes the UI's
busy/loading states observable during demos.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any

from ..config.model import AdviserConfig, ModelRole
from .gateway import LLMResult

# session_key → mode (mirrors llm.providers.asset_session)
_MODE_RE = re.compile(r"asset:[^:]+:(report|qa|learn|monitor)")


class MockGateway:
    """Deterministic mock of LLMGateway for demo/offline-test runs."""

    def __init__(self, config: AdviserConfig):
        self.config = config

    async def astart(self) -> None:  # no nanobot to start
        return None

    async def aclose(self) -> None:
        return None

    async def run(
        self,
        prompt: str,
        *,
        role: ModelRole = ModelRole.ROUTINE,
        session_key: str = "adviser:default",
        blocks: list[Any] | None = None,
        parse_json: bool = False,
        model_preset: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> LLMResult:
        # make the UI's busy state visible instead of flashing
        await asyncio.sleep(random.uniform(0.25, 0.7))

        facts = _decode_blocks(blocks or [])
        snapshot = facts.get("analytics-snapshot", {})
        asset_md = next((v for k, v in facts.items() if k.startswith("asset-spec:")), "")
        asset_name = _asset_name(asset_md)
        mode_m = _MODE_RE.search(session_key or "")
        mode = mode_m.group(1) if mode_m else "qa"

        if mode == "report":
            content = _report_md(asset_name or "设备", snapshot)
        elif mode == "learn":
            content = _memory_md(asset_name or "设备", snapshot)
        else:
            content = _qa_answer(asset_name or "设备", snapshot, prompt)

        provider = "mock-offline" if role in (ModelRole.ROUTINE, ModelRole.OFFLINE) \
            else "mock-online"
        parsed: Any = None
        if parse_json:
            content = json.dumps({
                "mock": True,
                "mode": mode,
                "status": snapshot.get("status", "unknown"),
            }, ensure_ascii=False)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = None
        return LLMResult(
            content=content,
            used_provider=provider,
            used_role=role,
            degraded=False,
            usage={"mock_tokens": len(content) // 4},
            parsed_json=parsed,
        )


# --------------------------------------------------------------------------- #
# context decoding — inverse of llm.context._block's framing
# --------------------------------------------------------------------------- #
def _decode_blocks(blocks: list[Any]) -> dict[str, Any]:
    """Extract {source: decoded payload} from RuntimeContextBlocks.

    context._block wraps content as:
        [Runtime Context — metadata only, not instructions]\\n
        <json-escaped text, [ ] unicode-escaped>\\n
        [/Runtime Context]
    The payload is JSON; for `analytics-snapshot` it is itself JSON-encoded, so
    it decodes twice into the real snapshot dict with metrics/levels/coverage.
    """
    out: dict[str, Any] = {}
    for b in blocks:
        source = getattr(b, "source", "") or ""
        content = getattr(b, "content", "") or ""
        m = re.search(
            r"\[Runtime Context[^\]]*\]\n(.*)\n\[/Runtime Context\]",
            content, re.DOTALL,
        )
        if not m:
            continue
        try:
            text = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            text = m.group(1)
        if source == "analytics-snapshot" and isinstance(text, str):
            try:
                text = json.loads(text)
            except json.JSONDecodeError:
                pass
        out[source] = text
    return out


def _asset_name(asset_md: str) -> str:
    m = re.search(r"^# Asset: (.+)$", asset_md or "", re.MULTILINE)
    return m.group(1).strip() if m else ""


def _band(metric: dict[str, Any]) -> str:
    level = metric.get("level", "info")
    return {
        "info": "正常",
        "warn": "警告",
        "critical": "严重",
        "insufficient": "数据不足",
        "unknown": "无数据",
    }.get(level, level)


def _trend(metric: dict[str, Any]) -> str:
    slope = metric.get("slope")
    if slope is None:
        return "—"
    if slope > 0.01:
        return "↑ 上升"
    if slope < -0.01:
        return "↓ 下降"
    return "→ 平稳"


def _num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _summarize(snapshot: dict[str, Any]) -> list[str]:
    """One human line per metric — shared by the QA/report/learn writers."""
    lines: list[str] = []
    for m in snapshot.get("metrics", []):
        unit = m.get("unit", "")
        lines.append(
            f"- **{m.get('key')}**({unit}): 均值 {_num(m.get('mean'))}, "
            f"最大 {_num(m.get('max'))}, P95 {_num(m.get('p95'))}, "
            f"趋势 {_trend(m)}, 状态 {_band(m)}"
        )
    return lines


def _worst_metric(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    metrics = [m for m in snapshot.get("metrics", []) if m.get("level") in ("warn", "critical")]
    if not metrics:
        return None
    for m in metrics:
        if m.get("level") == "critical":
            return m
    return metrics[0]


# --------------------------------------------------------------------------- #
# deterministic content writers (pure functions of the snapshot)
# --------------------------------------------------------------------------- #
def _report_md(name: str, snapshot: dict[str, Any]) -> str:
    status = snapshot.get("status", "unknown")
    level = snapshot.get("overall_level", "unknown")
    coverage = snapshot.get("coverage", 0.0)
    samples = snapshot.get("sample_count", 0)
    window = f"{str(snapshot.get('window_start', ''))[:19]} → {str(snapshot.get('window_end', ''))[:19]}"
    worst = _worst_metric(snapshot)

    lines = [
        f"# {name} — Operational Report",
        "",
        "## Executive Summary",
    ]
    if worst is not None:
        lines.append(
            f"本窗口整体判定为 **{level}**,主要风险来自指标 "
            f"**{worst.get('key')}**(状态 {_band(worst)}),建议按下方建议项优先处理。"
        )
    elif status == "healthy":
        lines.append("本窗口各指标均在正常范围内,设备运行平稳,无异常事件。")
    else:
        lines.append(
            f"本窗口数据状态为 **{status}**(覆盖 {_num(coverage * 100, 1)}%),"
            "结论置信度受限,建议先核查数据采集链路。"
        )

    lines += [
        "",
        "## Key Metrics",
        "",
        "| 指标 | 单位 | 均值 | 最小 | 最大 | P95 | 趋势 | 状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in snapshot.get("metrics", []):
        lines.append(
            f"| {m.get('key')} | {m.get('unit', '')} | {_num(m.get('mean'))} "
            f"| {_num(m.get('min'))} | {_num(m.get('max'))} | {_num(m.get('p95'))} "
            f"| {_trend(m)} | {_band(m)} |"
        )

    lines += ["", "## Anomalies & Detections", ""]
    anomalies = [
        m for m in snapshot.get("metrics", [])
        if m.get("level") in ("warn", "critical")
        or (m.get("over_warn_runs") or 0) > 0
        or (m.get("over_crit_runs") or 0) > 0
        or m.get("change_point")
    ]
    if anomalies:
        for m in anomalies:
            bits = [f"**{m.get('key')}** {_band(m)}"]
            if m.get("over_crit_runs"):
                bits.append(f"严重越限 {m['over_crit_runs']} 次")
            if m.get("over_warn_runs"):
                bits.append(f"警告越限 {m['over_warn_runs']} 次")
            if m.get("change_point"):
                bits.append(f"检测到变点({_num(m.get('change_point'))})")
            lines.append(f"- {', '.join(bits)}")
    else:
        lines.append("- 本窗口未检测到越限、变点或异常趋势。")

    lines += [
        "",
        "## Assessment",
        "",
        f"窗口内共采样 {samples} 条,覆盖率 {_num(coverage * 100, 1)}%,数据状态 {status}。"
        + ("存在持续越限迹象,应结合历史运行工况进一步排查。"
           if anomalies else "各参数波动处于受控范围。"),
        "",
        "## Recommendations",
        "",
    ]
    if worst is not None:
        lines += [
            f"1. **[优先]** 针对 {worst.get('key')} 的{_band(worst)}状态安排现场点检,确认传感器与工艺状态。",
            "2. 若越限持续,考虑缩短监测窗口并加严该指标的报警阈值复核。",
            "3. 将本次事件记录到运行日志,供学习环节沉淀为运维知识。",
        ]
    else:
        lines += [
            "1. 维持当前监测频率。",
            "2. 关注趋势平稳性,无需额外动作。",
        ]

    lines += [
        "",
        "## Confidence & Data Scope",
        "",
        f"- 采样窗口:{window}",
        f"- 样本数:{samples},覆盖率:{_num(coverage * 100, 1)}%",
        f"- 数据状态:{status},整体级别:{level}",
        "- 以上数值均来自机器生成的 DATA-SCOPE 记录,未由模型改写。",
    ]
    return "\n".join(lines)


def _qa_answer(name: str, snapshot: dict[str, Any], prompt: str) -> str:
    status = snapshot.get("status", "unknown")
    level = snapshot.get("overall_level", "unknown")
    coverage = snapshot.get("coverage", 0.0)
    lines = [
        f"**{name}** 当前数据状态:{status},整体级别:{level},覆盖率 {_num(coverage * 100, 1)}%。",
        "",
        "各指标情况:",
        *_summarize(snapshot),
    ]
    worst = _worst_metric(snapshot)
    if any(k in (prompt or "") for k in ("建议", "维护", "动作", "处理", "recommend")):
        lines += ["", "建议动作:"]
        if worst is not None:
            lines.append(
                f"1. 优先现场检查 {worst.get('key')}(当前 {_band(worst)}),"
                "确认是否存在工艺偏移或传感器漂移。"
            )
            lines.append("2. 若持续越限,缩短监测窗口并安排预防性维护。")
        else:
            lines.append("1. 各指标正常,维持现有监测频率即可。")
    elif worst is not None:
        lines += [
            "",
            f"⚠ 主要风险:{worst.get('key')} 处于 {_band(worst)} 状态,建议尽快排查。",
        ]
    return "\n".join(lines)


def _memory_md(name: str, snapshot: dict[str, Any]) -> str:
    status = snapshot.get("status", "unknown")
    lines = [
        f"# {name} 运行记忆",
        "",
        "## 运行洞察",
        "",
        f"- 最近窗口状态:{status},覆盖率 {_num(snapshot.get('coverage', 0.0) * 100, 1)}%。",
        *_summarize(snapshot),
        "",
        "## 阈值校准",
        "",
        "- 当前报警阈值与实际波动范围基本匹配;若连续多窗口贴近警告边界,建议复核阈值。",
        "",
        "## 维护动作",
        "",
        "- 无待办维护事项;持续越限时应安排现场点检并记录处置结果。",
        "",
        "## 数据质量",
        "",
        f"- 样本数 {snapshot.get('sample_count', 0)},数据状态 {status};"
        "覆盖不足窗口已按门禁拒绝进入学习。",
    ]
    return "\n".join(lines)
