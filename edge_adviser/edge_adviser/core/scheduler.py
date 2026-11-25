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
from typing import Any, Callable

from loguru import logger

from ..analytics import analyze, empty_snapshot
from ..analytics.gates import classify_snapshot_state, gate_snapshot_for_read, trust_from_state
from ..config.model import AdviserConfig, AssetSpec, AssetState, ModelRole, SourceSpec
from ..core.asset_store import AssetStore
from ..core.runlog import log_run_event
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
    consecutive_empty: int = 0  # N4: how many back-to-back empty windows
    # cron slot bookkeeping: the last minute-slot each cron job fired in, so a
    # job runs at most once per matching slot even across multiple ticks
    last_deep_slot: Any = None
    last_learn_slot: Any = None


@dataclass
class Supervisor:
    config: AdviserConfig
    store: AssetStore
    gateway: LLMGateway
    report_gen: ReportGenerator
    learner: Learner
    monitor_interval_s: float = 60.0
    # N9: regime label injected by the caller (replaces the hardcoded "degrade").
    default_regime: str | None = None
    credential_lookup: Callable[[str], dict[str, str]] | None = None
    _sources: dict[str, DataSource] = field(default_factory=dict)
    _state: dict[str, ScheduleState] = field(default_factory=dict)
    # per-asset job mutex: a manual run-now/report/learn must not execute the
    # same window concurrently with a scheduler tick (two snapshots in one
    # window, double LLM spend, interleaved runlog). Locks are created lazily
    # per asset and never removed (bounded by the asset count).
    _asset_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def _lock_for(self, asset_id: str) -> asyncio.Lock:
        lock = self._asset_locks.get(asset_id)
        if lock is None:
            lock = self._asset_locks[asset_id] = asyncio.Lock()
        return lock

    async def start(self) -> None:
        await self.gateway.astart()
        for spec in self._iter_sources():
            if spec.asset_id not in self._sources:
                try:
                    self._sources[spec.asset_id] = self.prepare_source(spec)
                except NotImplementedError as exc:
                    # Keep configured-but-unavailable sources visible in the audit trail.
                    logger.warning("source not available for {a}: {e}", a=spec.asset_id, e=exc)
                    log_run_event(self.store, spec.asset_id, "serve_start", "no_data",
                                  reason=f"source_unwired: {spec.kind.value}",
                                  detail=str(exc)[:200])
                except Exception as exc:
                    # 0.5.2: any other construction failure (bad MQTT URL, missing
                    # OPC-UA cert, invalid Modbus register map — the 0.5.0
                    # validators raise ValueError) must isolate the BAD ASSET,
                    # not prevent the whole HMI from coming up. The asset's run
                    # path archives empty windows → offline state machine →
                    # alert, so the failure stays visible to the operator.
                    logger.warning("source init failed for {a}: {e}", a=spec.asset_id, e=exc)
                    log_run_event(self.store, spec.asset_id, "serve_start", "no_data",
                                  reason=f"source_failed: {spec.kind.value}",
                                  detail=str(exc)[:200])
        # P4: warn + audit any asset with zero snapshots at startup so a cold
        # asset is never silently treated as healthy.
        for asset in self.config.assets:
            if not asset.enabled:
                continue
            if not self.store.list_snapshots(asset.asset_id, limit=1):
                msg = f"asset {asset.asset_id} has no snapshots at startup"
                logger.warning(msg)
                log_run_event(self.store, asset.asset_id, "serve_start",
                              "no_data", reason="zero_snapshots", detail=msg)

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
            if not asset.enabled:
                continue
            spec = next((s for s in self.config.sources if s.asset_id == asset.asset_id), None)
            if spec is None:
                logger.warning("no source for asset {a}; skipping", a=asset.asset_id)
                continue
            yield spec

    def prepare_source(self, spec: SourceSpec) -> DataSource:
        credentials = None
        if spec.credential_id and self.credential_lookup is not None:
            credentials = self.credential_lookup(spec.credential_id)
        return create_source(spec, credentials=credentials)

    async def install_source(self, asset_id: str, replacement: DataSource) -> str | None:
        """Swap a prepared source and close the previous connection."""
        previous = self._sources.get(asset_id)
        self._sources[asset_id] = replacement
        if previous is not None:
            try:
                await previous.aclose()
            except Exception as exc:
                logger.warning("old source close failed for {a}: {e}", a=asset_id, e=exc)
                return "旧连接器关闭异常，建议重启服务确认连接释放"
        return None

    async def replace_source(self, spec: SourceSpec) -> None:
        """Create a replacement before swapping, preserving the active source on failure."""
        replacement = self.prepare_source(spec)
        await self.install_source(spec.asset_id, replacement)

    async def remove_source(self, asset_id: str) -> str | None:
        previous = self._sources.pop(asset_id, None)
        if previous is not None:
            try:
                await previous.aclose()
            except Exception as exc:
                logger.warning("source close failed for {a}: {e}", a=asset_id, e=exc)
                return "连接器关闭异常，建议重启服务确认连接释放"
        return None

    def _save_and_prune(self, asset_id: str, snap_dict: dict[str, Any]) -> None:
        """Archive a snapshot with configured retention (best-effort pruning)."""
        self.store.save_snapshot(asset_id, snap_dict, keep=self.config.max_snapshots)
        if self.config.runlog_enabled and self.config.max_runlog_rows > 0:
            try:
                self.store.truncate_jsonl(self.store.runlog_path(asset_id),
                                          self.config.max_runlog_rows)
            except Exception:  # noqa: BLE001 — pruning must never break a run
                pass

    async def _save_and_prune_async(self, asset_id: str, snap_dict: dict[str, Any]) -> None:
        """Thread-pool variant of _save_and_prune.

        Snapshot serialization + retention pruning are blocking file I/O on the
        scheduler's event loop; with many assets (or big windows) they stall the
        SSE heartbeat and every API handler sharing the loop. Offloading keeps
        the loop responsive while the durable write happens in a worker thread.
        """
        await asyncio.to_thread(self._save_and_prune, asset_id, snap_dict)

    async def _archive_empty(self, asset: AssetSpec, st: ScheduleState,
                             now: "Any", started: float, *, reason: str,
                             kind: str) -> dict[str, Any]:
        """Archive an empty-window snapshot, advance the offline counter, audit.

        Shared by the pull-failed, empty-window, and analyze-failed paths so all
        three produce the same auditable record and drive the OFFLINE state
        machine identically.
        """
        import datetime as _dt
        st.consecutive_empty += 1
        state = classify_snapshot_state(
            asset, {"sample_count": 0}, st.consecutive_empty, now, self.config)
        snap_dict = empty_snapshot(asset, now, state=state)
        await self._save_and_prune_async(asset.asset_id, snap_dict)
        self.learner.buffer_snapshot(asset.asset_id, snap_dict)
        log_run_event(self.store, asset.asset_id, "run_once", kind,
                      reason=f"{reason}; consecutive={st.consecutive_empty}",
                      state=state.value)
        snap_dict["_elapsed_s"] = round(time.time() - started, 3)
        snap_dict.setdefault("error", reason if kind == "exception" else "")
        return snap_dict

    async def run_once(self, asset: AssetSpec, src: DataSource | None = None) -> dict[str, Any]:
        """Pull, analyze, archive a single window for one asset.

        Now data-integrity-aware: empty windows are archived as an
        INSUFFICIENT/OFFLINE snapshot (audit + state machine), the consecutive-empty
        counter drives the offline verdict, and a runlog entry records the outcome.
        Serialized per asset against run_deep_report/run_learn so a manual
        trigger cannot interleave with a scheduler tick on the same asset.
        """
        async with self._lock_for(asset.asset_id):
            return await self._run_once_locked(asset, src)

    async def _run_once_locked(self, asset: AssetSpec,
                               src: DataSource | None = None) -> dict[str, Any]:
        import datetime as _dt
        started = time.time()
        st = self._state.setdefault(asset.asset_id, ScheduleState())
        src = src or self._sources.get(asset.asset_id)
        if src is None:
            log_run_event(self.store, asset.asset_id, "run_once", "no_data",
                          reason="no_source")
            return {"asset_id": asset.asset_id, "error": "no source"}
        regime = self.default_regime or self.config.analysis_regime  # N9
        now = _dt.datetime.now(_dt.timezone.utc)
        start = now - _dt.timedelta(seconds=asset.window_s)
        try:
            samples = await src.pull(start, now)
        except Exception as exc:
            # treat a failed pull exactly like an empty window: archive the empty
            # snapshot and advance the state machine so persistent failures still
            # reach OFFLINE and leave an audit trail instead of vanishing silently.
            return await self._archive_empty(asset, st, now, started,
                                       reason=f"pull failed: {exc}", kind="exception")
        if not samples:
            return await self._archive_empty(asset, st, now, started,
                                       reason="empty_window", kind="no_data")
        try:
            st.consecutive_empty = 0
            # numpy statistics can take real CPU time on large windows — run in
            # a worker thread so the shared event loop (SSE heartbeat, API
            # handlers) is not stalled mid-analysis
            snapshot = await asyncio.to_thread(
                analyze, asset, samples, source=src.kind, regime=regime,
                window_start=start.isoformat(timespec="seconds"),
                window_end=now.isoformat(timespec="seconds"))
        except Exception as exc:
            # analysis must never escape run_once: in web mode the loop swallows
            # exceptions, which would drop the asset from monitoring with no
            # snapshot, no runlog entry, and no offline transition.
            return await self._archive_empty(asset, st, now, started,
                                       reason=f"analyze failed: {exc}", kind="exception")
        snap_dict = snapshot.to_dict()
        await self._save_and_prune_async(asset.asset_id, snap_dict)
        self.learner.buffer_snapshot(asset.asset_id, snap_dict)
        log_run_event(self.store, asset.asset_id, "run_once", "ok",
                      reason="", state=snap_dict.get("status"))
        snap_dict["_elapsed_s"] = round(time.time() - started, 3)
        return snap_dict

    async def run_monitor(self, asset: AssetSpec) -> LLMResult:
        """Lightweight routine monitor using the cheap/local model.

        Gated on a fresh snapshot: without one, we refuse to drive the model and
        record the non-production in the runlog so it is traceable.
        """
        gate = gate_snapshot_for_read(self.store, asset, self.config)
        if not gate.ok:
            log_run_event(self.store, asset.asset_id, "monitor", "no_data",
                          reason=f"no_fresh_snapshot: {gate.reason}", state=gate.state.value)
            return LLMResult(content="", error=f"no_fresh_snapshot: {gate.reason}")
        snap = gate.snapshot or {}
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
        """Full deep report via strong model + archive.

        Gated on a fresh snapshot; a missing/stale snapshot is recorded in the
        runlog so a day with no report is traceable (P2). Serialized per asset
        against run_once/run_learn (same-window double execution).
        """
        async with self._lock_for(asset.asset_id):
            return await self._run_deep_report_locked(asset)

    async def _run_deep_report_locked(self, asset: AssetSpec) -> dict[str, Any]:
        gate = gate_snapshot_for_read(self.store, asset, self.config)
        if not gate.ok:
            log_run_event(self.store, asset.asset_id, "deep_report", "no_data",
                          reason=f"no_fresh_snapshot: {gate.reason}", state=gate.state.value)
            return {"asset_id": asset.asset_id,
                    "error": f"no_fresh_snapshot: {gate.reason}",
                    "state": gate.state.value}
        snap = gate.snapshot or {}
        result = await self.report_gen.generate(asset, snap, role=ModelRole.ONLINE)
        # A provider failure produced no interpretation — archiving an "(empty)"
        # report would fake a successful day. Record the failure in the runlog
        # instead so "no report" stays traceable (P2) without polluting the
        # report archive.
        if result.error and not result.content.strip():
            log_run_event(self.store, asset.asset_id, "deep_report", "provider_failed",
                          reason=result.error)
            return {"asset_id": asset.asset_id,
                    "error": f"provider_failed: {result.error}",
                    "state": gate.state.value}
        outdir = self.report_gen.archive(
            asset, snap, result.content or "(empty)",
            source="", degraded=result.degraded, provider=result.used_provider,
        )
        return {"asset_id": asset.asset_id, "report_dir": str(outdir),
                "degraded": result.degraded, "error": result.error}

    async def run_learn(self, asset: AssetSpec) -> LLMResult:
        async with self._lock_for(asset.asset_id):
            return await self.learner.learn_asset(asset, role=ModelRole.ONLINE)

    async def run_due_jobs(self, asset: AssetSpec, now: "Any") -> None:
        """Fire deep-report / learn when their cron expressions are due.

        The configured ``deep_report_cron`` / ``learn_cron`` promise scheduled
        runs; this is what actually honors them. Both CLI ``serve`` and the web
        scheduler loop call this once per tick per asset. A job runs at most
        once per cron-slot: the last-fired wall clock must be strictly before
        the slot boundary this tick belongs to. Errors are logged and audited,
        never raised into the calling loop.
        """
        from ..core.cron import cron_matches

        st = self._state.setdefault(asset.asset_id, ScheduleState())
        slot = now.replace(second=0, microsecond=0)
        fired_deep = fired_learn = False
        try:
            if self.config.deep_report_cron and \
                    cron_matches(self.config.deep_report_cron, now) and \
                    (st.last_deep_slot is None or st.last_deep_slot < slot):
                st.last_deep_slot = slot
                fired_deep = True
                await self.run_deep_report(asset)
        except Exception as exc:  # noqa: BLE001 — cron jobs must not kill the loop
            logger.error("deep report failed for {a}: {e}", a=asset.asset_id, e=exc)
            log_run_event(self.store, asset.asset_id, "deep_report", "exception",
                          reason=str(exc)[:200])
        try:
            if self.config.learn_cron and \
                    cron_matches(self.config.learn_cron, now) and \
                    (st.last_learn_slot is None or st.last_learn_slot < slot):
                st.last_learn_slot = slot
                fired_learn = True
                await self.run_learn(asset)
        except Exception as exc:  # noqa: BLE001
            logger.error("learn failed for {a}: {e}", a=asset.asset_id, e=exc)
            log_run_event(self.store, asset.asset_id, "learn", "exception",
                          reason=str(exc)[:200])
        if fired_deep:
            st.last_deep = time.time()
        if fired_learn:
            st.last_learn = time.time()


async def _main(supervisor: Supervisor) -> None:
    """Reference recurring loop: monitor every tick + cron-scheduled jobs.

    The CLI ``serve`` command and the web scheduler loop each implement this
    pattern with their own delivery concerns (printing / bus publishing); this
    helper stays for SDK-style embedding.
    """
    import datetime as _dt
    await supervisor.start()
    try:
        while True:
            for asset in supervisor.config.assets:
                if not asset.enabled:
                    continue
                try:
                    await supervisor.run_once(asset)
                except Exception as exc:  # noqa: BLE001
                    logger.error("run failed for {a}: {e}", a=asset.asset_id, e=exc)
                await supervisor.run_due_jobs(asset, _dt.datetime.now(_dt.timezone.utc))
            await asyncio.sleep(supervisor.monitor_interval_s)
    finally:
        await supervisor.stop()