"""Historical learning loop — consolidates accumulated data into durable domain
knowledge (`MEMORY.md`) per asset using nanobot's Dream-style memory machinery.

The learner consumes:
  * the asset's MEMORY.md (existing knowledge),
  * operator feedback (feedback.jsonl),
  * a set of recent snapshots,
  * the latest report conclusion,
and asks the LLM to refine MEMORY.md into a living knowledge base for that asset.
New knowledge is appended (or existing sections amended); old or contradicted
facts are flagged. Everything is written back versioned so it is an auditable,
revisable knowledge asset.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from ..analytics.gates import snapshot_age_s
from ..config.model import AdviserConfig, AssetSpec, AssetState, ModelRole
from ..core.asset_store import AssetStore
from ..core.runlog import log_run_event
from ..llm.gateway import LLMGateway, LLMResult

_LEARN_PROMPT = """You maintain the long-term domain knowledge base for an industrial
asset. Below is the current knowledge (MEMORY.md), recent operator feedback, and
a snapshot of recent data readings. Update the knowledge base:

- Add new operational insights (observed limits, failure precursor signals,
  effective maintenance actions, correlations between metrics).
- Correct anything contradicted by the feedback/data.
- Keep entries concise, evidence-grounded, and prioritized by importance.
- Do NOT invent measurement values that are not in the data.

Under NO circumstances return commentary. Return the ENTIRE new MEMORY.md
content (markdown) as your final message.

---
{confidence_note}
## Existing MEMORY.md
{memory}

## Recent operator feedback
{feedback}

## Recent analytics snapshot (summary)
{snapshot_summary}

## Latest report conclusion
{prev_conclusion}
"""


class Learner:
    def __init__(self, config: AdviserConfig, store: AssetStore, gateway: LLMGateway):
        self.config = config
        self.store = store
        self.gateway = gateway
        self._buffer: dict[str, list[dict[str, Any]]] = {}

    def buffer_snapshot(self, asset_id: str, snapshot: dict[str, Any], max_buf: int = 20) -> None:
        """Accumulate recent snapshots; keeps last `max_buf` per asset."""
        buf = self._buffer.setdefault(asset_id, [])
        buf.append(snapshot)
        if len(buf) > max_buf:
            del buf[: len(buf) - max_buf]

    async def learn_asset(self, asset: AssetSpec, *, role: ModelRole = ModelRole.ONLINE) -> LLMResult:
        """Run one learning pass over an asset.

        The gateway input is gated so untrustworthy data (offline / stale /
        insufficient) never gets written back into MEMORY.md as if it were
        learned knowledge (N7).
        """
        asset_id = asset.asset_id
        # N6/N8: when the keyword index / conflict ledger is enabled, write back
        # through MemoryService so MEMORY.md is git-versioned and claims tracked.
        from ..core.memory_service import MemoryService
        use_mem_svc = (self.config.memory_index == "keywords"
                       or self.config.memory_conflict_policy != "off")
        mem_svc = MemoryService(
            asset, self.config.workspace,
            index="keywords" if self.config.memory_index == "keywords" else "off",
            conflict_policy=self.config.memory_conflict_policy,
            decay_s=self.config.memory_decay_s,
        ) if use_mem_svc else None
        memory = mem_svc.read() if mem_svc else self.store.read_memory_md(asset_id)
        feedback = self.store.read_feedback(asset_id)
        snapshots = self._buffer.get(asset_id, []) or self._read_recent_snapshots(asset_id)
        prev_conclusion = self._prev_conclusion(asset_id)
        if not snapshots:
            logger.info("learn: no snapshots for {asset}; skipping", asset=asset_id)
            log_run_event(self.store, asset_id, "learn", "no_data", reason="no_snapshots")
            return LLMResult(content="", error="no snapshots to learn from")

        # ---- N7 gate ---- #
        gate_state, confidence_note = self._learn_gate(asset, snapshots[-1])
        if gate_state == "skip":
            log_run_event(self.store, asset_id, "learn", "gated",
                          reason=f"untrustworthy input: {confidence_note}", state=confidence_note)
            return LLMResult(content="", error=f"insufficient_data: {confidence_note}")
        if gate_state == "strict":
            log_run_event(self.store, asset_id, "learn", "gated",
                          reason=confidence_note, state=confidence_note)
            return LLMResult(content="", error=f"insufficient_data: {confidence_note}")

        feedback_text = _fmt_feedback(feedback) or "(none)"
        snap_text = _fmt_snapshot_summary(snapshots)
        prompt = _LEARN_PROMPT.format(
            memory=memory or "(empty knowledge base)",
            feedback=feedback_text,
            snapshot_summary=snap_text,
            prev_conclusion=prev_conclusion or "(no previous report)",
            confidence_note=confidence_note,
        )
        res = await self.gateway.run(
            prompt,
            role=role,
            session_key=f"asset:{asset_id}:learn",
        )
        if res.error or not res.content.strip():
            logger.warning("learn: provider failed for {asset}: {err}", asset=asset_id, err=res.error)
            log_run_event(self.store, asset_id, "learn", "provider_failed",
                          reason=(res.error or "empty output")[:200])
            return res
        # persist new knowledge, versioned (only reached when the gate passed)
        new_memory = res.content.strip() + "\n"
        if mem_svc:
            mem_svc.write(new_memory)  # versioned + claims recorded (N6/N8)
        else:
            self.store.write_memory_md(asset_id, new_memory)
        log_run_event(self.store, asset_id, "learn", "ok", reason="")
        logger.info("learn: updated MEMORY.md for {asset}", asset=asset_id)
        return res

    def _learn_gate(self, asset: AssetSpec, latest: dict) -> tuple[str, str]:
        """Return (verdict, confidence_note) for the learn gate (N7).

        verdict: "go" (learn normally), "soft" (learn but label low-confidence),
        "skip" (no snapshots / offline / stale / planned downtime — unrecoverable),
        "strict" (low coverage / insufficient but policy=strict).
        """
        config = self.config
        state_raw = latest.get("status")
        try:
            state = AssetState(state_raw) if state_raw else AssetState.UNKNOWN
        except ValueError:
            state = AssetState.UNKNOWN  # fail closed: unrecognized status ≠ healthy

        hard_skip = state in (AssetState.OFFLINE, AssetState.UNKNOWN,
                              AssetState.STALE, AssetState.PLANNED_DOWNTIME)
        if hard_skip:
            return "skip", f"state={state.value}"

        if snapshot_age_s(latest) > config.data_freshness_s:
            return "skip", "data past freshness window"

        coverage = float(latest.get("coverage", 1.0) or 1.0)
        below_coverage = coverage < config.coverage_threshold
        is_insufficient = state == AssetState.INSUFFICIENT or \
            (latest.get("overall_level") in ("insufficient", "unknown"))

        note = f"Input data is low-confidence (state={state.value}, coverage={coverage:.2f}). " \
               "Any conclusions must be marked with the deterministic prefix " \
               "'[confidence: low]' and must NOT be stated as verified fact."
        if config.learn_gate == "off":
            return "go", ""
        if config.learn_gate == "strict" and (is_insufficient or below_coverage):
            return "strict", note.strip()
        if is_insufficient or below_coverage:
            return "soft", note.strip()
        return "go", ""

    def _read_recent_snapshots(self, asset_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in self.store.list_snapshots(asset_id, limit=12):
            try:
                import json
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    def _prev_conclusion(self, asset_id: str) -> str:
        # iterate reports dir newest; fall back to extracting from report.md for
        # archives written before the conclusion was persisted into snapshot.json
        files = sorted(self.store.reports_dir(asset_id).glob("*/snapshot.json"))
        if not files:
            return ""
        try:
            import json
            doc = json.loads(files[-1].read_text(encoding="utf-8"))
            conclusion = str(doc.get("conclusion", ""))[:500]
            if conclusion:
                return conclusion
        except Exception:
            pass
        try:
            from ..report.generator import _extract_conclusion
            report_md = files[-1].parent / "report.md"
            if report_md.exists():
                return _extract_conclusion(report_md.read_text(encoding="utf-8"))[:500]
        except Exception:
            pass
        return ""


def _fmt_feedback(feedback: list[dict[str, Any]], limit: int = 10) -> str:
    if not feedback:
        return ""
    lines = []
    for f in feedback[-limit:]:
        lines.append(f"- [{f.get('timestamp','?')}] {f.get('note','')}")
    return "\n".join(lines)


def _fmt_snapshot_summary(snapshots: list[dict[str, Any]], limit: int = 6) -> str:
    lines = []
    for s in snapshots[-limit:]:
        lines.append(f"- {s.get('window_start','?')}~{s.get('window_end','?')} "
                     f"level={s.get('overall_level')}: {s.get('headline','')}")
    return "\n".join(lines)