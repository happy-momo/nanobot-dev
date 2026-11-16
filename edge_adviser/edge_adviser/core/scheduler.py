"""Supervisor — orchestrates the recurring workload for all assets.

Runs an async event loop that, for each configured asset:
  * pulls a fresh data window from its data source,
  * computes an AnalyticsSnapshot,
  * buffers it for the learner,
  * archives the snapshot,
  * emits a lightweight monitor message (routine / local model) when the data
    warrants attention or on a cadence,
  * triggers a deep report on a schedule (cron),
  * runs the learn loop on its own schedule.

The Supervisor is intentionally simple (no external scheduler): an asyncio tick
loop with per-asset cadence. Cron parsing for deep-report / learn uses stdlib
`croniter`-substitute via a minimal matcher to avoid extra deps.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ..analytics import analyze
from ..config.model import AdviserConfig, AssetSpec, ModelRole, SourceSpec
from ..core.asset_store import AssetStore
from ..ingest import create_source
from ..ingest.base import DataSource
from ..llm.gateway import LLMGateway, LLMResult, ProviderUnavailable
from ..llm.providers import asset_session
from ..report.generator import ReportGenerator
from ..learn.learner import Learner


@dataclass
class ScheduleState:
    last_monitor: float = 0.0
    last_deep: float = 0.0
    last_learn: float = 0.0
    next_learn_ok: bool = False


@dataclass
class Supervisor:
    config: AdviserConfig
    store: AssetStore
    gateway: LLMGateway
    report_gen: ReportGenerator
    learner: Learner
    monitor_interval_s: float = 60.0
    _sources: dict[str, DataSource] = field(default_factory=dict)
    _state: dict[str, ScheduleState] = field(default_factory=dict)

    async def start(self) -> None:
        await self.gateway.astart()
        for spec in self._iter_sources():
            if spec.asset_id not in self._sources:
                try:
                    self._sources[spec.asset_id] = create_source(spec)
                except NotImplementedError as exc:
                    logger.warning("source not available for {a}: {e}", a=spec.asset_id, e=exc)

    async def stop(self) -> None:
        for src in self._sources.values():
            try:
                await src.aclose()
            except Exception:
                pass
        self._sources.clear()
        await self.gateway.aclose()

    def _iter_sources(self):
        # one source per asset (first match); if none configured, warn
        for asset in self.config.assets:
            spec = next((s for s in self.config.sources if s.asset_id == asset.asset_id), None)
            if spec is None:
                logger.warning("no source for asset {a}; skipping", a=asset.asset_id)
                continue
            yield spec

    async def run_once(self, asset: AssetSpec, src: DataSource | None = None) -> dict[str, Any]:
        """Pull, analyze, archive a single window for one asset."""
        started = time.time()
        src = src or self._sources.get(asset.asset_id)
        if src is None:
            return {"asset_id": asset.asset_id, "error": "no source"}
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        end = now
        start = now - _dt.timedelta(seconds=asset.window_s)
        samples = await src.pull(start, end)
        if not samples:
            return {"asset_id": asset.asset_id, "error": "no samples"}
        snapshot = analyze(asset, samples, source=src.kind, regime="degrade")
        snap_dict = snapshot.to_dict()
        self.store.save_snapshot(asset.asset_id, snap_dict)
        self.learner.buffer_snapshot(asset.asset_id, snap_dict)
        snap_dict["_elapsed_s"] = round(time.time() - started, 3)
        return snap_dict

    async def run_monitor(self, asset: AssetSpec) -> LLMResult:
        """Lightweight routine monitor using the cheap/local model."""
        snap = await self._latest(asset)
        if not snap:
            return LLMResult(content="", error="no snapshot")
        question = ("Routine monitoring: summarize any change worth attention in this "
                    f"asset's latest snapshot. Overall level: {snap.get('overall_level')}. "
                    "Use only these facts. Be brief.")
        from ..qa.engine import QAEngine
        qa = QAEngine(self.config, self.store, self.gateway)
        return await qa.ask(asset, question, role=ModelRole.ROUTINE)

    async def _latest(self, asset: AssetSpec) -> dict[str, Any] | None:
        files = self.store.list_snapshots(asset.asset_id, limit=1)
        if not files:
            return None
        try:
            import json
            return json.loads(files[0].read_text(encoding="utf-8"))
        except Exception:
            return None

    async def run_deep_report(self, asset: AssetSpec) -> dict[str, Any]:
        """Full deep report via strong model + archive."""
        snap = await self._latest(asset)
        if not snap:
            return {"asset_id": asset.asset_id, "error": "no snapshot"}
        result = await self.report_gen.generate(asset, snap, role=ModelRole.ONLINE)
        outdir = self.report_gen.archive(
            asset, snap, result.content or "(empty)",
            source="", degraded=result.degraded, provider=result.used_provider,
        )
        return {"asset_id": asset.asset_id, "report_dir": str(outdir),
                "degraded": result.degraded, "error": result.error}

    async def run_learn(self, asset: AssetSpec) -> LLMResult:
        return await self.learner.learn_asset(asset, role=ModelRole.ONLINE)


async def _main(supervisor: Supervisor) -> None:
    """Reccurring loop (kept here as a helper; the CLI drives it)."""
    await supervisor.start()
    try:
        while True:
            for asset in supervisor.config.assets:
                st = supervisor._state.setdefault(asset.asset_id, ScheduleState())
                now = time.time()
                if now - st.last_deep >= 3600 * 24:
                    try:
                        await supervisor.run_deep_report(asset)
                    except Exception as exc:
                        logger.error("deep report failed: {e}", e=exc)
                    st.last_deep = now
            await asyncio.sleep(supervisor.monitor_interval_s)
    finally:
        await supervisor.stop()