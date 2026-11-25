"""Compose the runtime services behind the FastAPI HMI server.

This mirrors ``cli._build``/``cli._supervisor`` so the web layer reuses exactly the
same objects the CLI drives. The server never mutates the fact spine; it only
exposes and triggers the existing deterministic services.

The HMI keeps the "deterministic facts vs LLM interpretation" split visible:
read-side endpoints return snapshots as ground truth, action endpoints always go
through the freshness gate (``gate_snapshot_for_read``) so a missing/stale
snapshot is refused instead of driving the model to guess.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from ..config import load_config
from ..config.model import AdviserConfig
from ..core.asset_store import AssetStore
from ..core.bus import EventBus
from ..core.credential_vault import CredentialVault
from ..core.scheduler import Supervisor
from ..learn import FeedbackService
from ..learn.learner import Learner
from ..llm.context import ContextBuilder
from ..llm.gateway import LLMGateway
from ..qa.engine import QAEngine
from ..report.generator import ReportGenerator


class AppState:
    """Hold every service + the event bus so routers can inject just what they need."""

    def __init__(self, config: AdviserConfig, store: AssetStore, gateway: Any | None = None):
        self.config = config
        self.store = store
        self.configuration_lock = asyncio.Lock()
        self.credential_vault = CredentialVault(
            store.workspace / ".connector_credentials.enc",
            os.environ.get("EDGE_ADVISER_CREDENTIAL_KEY"),
        )

        self.bus = EventBus(replay=1)

        # scheduler liveness: the web loop stamps a monotonic heartbeat each
        # tick so /api/health can distinguish "server up but scheduler dead"
        # from a healthy service (a watchdog probing a static "ok" can't).
        import time as _time
        self.last_tick: float = 0.0
        self._time = _time

        ctx = ContextBuilder(config, store)
        # allow a stub gateway to be injected for offline tests; the real
        # LLMGateway is the default production wiring. Demo mode swaps in a
        # deterministic mock so QA/reports/learn work with no nanobot/LLM at all.
        if gateway is None:
            if config.demo:
                from ..llm.mock import MockGateway
                gateway = MockGateway(config)
            else:
                gateway = LLMGateway(config, ctx)
        self.gateway = gateway
        self.report_gen = ReportGenerator(config, store, self.gateway)
        self.learner = Learner(config, store, self.gateway)
        self.qa = QAEngine(config, store, self.gateway)
        self.feedback = FeedbackService(store)

        self.supervisor = Supervisor(
            config=config,
            store=store,
            gateway=self.gateway,
            report_gen=self.report_gen,
            learner=self.learner,
            default_regime=config.analysis_regime,
            credential_lookup=self.credential_vault.get,
        )

    def heartbeat(self) -> None:
        """Stamp the scheduler-loop liveness (called once per tick)."""
        self.last_tick = self._time.time()

    def tick_age_s(self) -> float:
        """Seconds since the last scheduler tick (inf before the first tick)."""
        if not self.last_tick:
            return float("inf")
        return self._time.time() - self.last_tick

    async def start(self) -> None:
        await self.supervisor.start()

    async def stop(self) -> None:
        await self.supervisor.stop()
        await self.bus.aclose()


def build_state(config_path: str | Path, gateway: Any | None = None,
                demo: bool | None = None) -> AppState:
    """Load the config from disk and build the full service graph.

    The workspace is resolved against the config file's directory so a daemon or
    manual launch is consistent regardless of the process CWD (fixes a CLI trap).

    ``demo`` forces demo mode BEFORE the service graph is built — the gateway
    choice (MockGateway vs LLMGateway) is made inside AppState, so setting
    ``config.demo`` afterwards would leave a real gateway wired while claiming
    to be a demo run.
    """
    cfg = load_config(config_path)
    if demo is not None:
        cfg.demo = bool(demo)
    if not cfg.workspace.is_absolute():
        base = Path(config_path).resolve().parent
        cfg.workspace = (base / cfg.workspace).resolve()
    # Container deployments point the workspace at a mounted volume without
    # editing the config: EDGE_ADVISER_WORKSPACE overrides whatever the file says
    # (same rule as cli._build — both entry points honor the override).
    import os
    env_ws = os.environ.get("EDGE_ADVISER_WORKSPACE")
    if env_ws:
        cfg.workspace = Path(env_ws)
    store = AssetStore(cfg.workspace)
    for asset in cfg.assets:
        if asset.enabled:
            store.ensure_asset(asset)
    return AppState(cfg, store, gateway=gateway)


async def publish_error(state: AppState, kind: str, asset_id: str, error: str, **extra: Any) -> None:
    """Best-effort bus publish that never raises into a request handler."""
    try:
        await state.bus.publish(kind, asset_id, payload={"error": error, **extra})
    except Exception:  # noqa: BLE001
        pass