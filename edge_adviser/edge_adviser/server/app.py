"""FastAPI application for the EdgeAdviser HMI.

Assembles the same services the CLI drives, starts the Supervisor as a background
asyncio task, hosts the built Vue SPA, and exposes REST + SSE.

Lifecycle (lifespan):
  * on startup: build AppState, start the supervisor (gateway + sources), and run a
    scheduler loop task that periodically pulls a fresh window per asset.
  * on shutdown: cancel the loop, stop the supervisor, close the bus.

The scheduler loop is the web-mode equivalent of ``cli._cmd_serve`` — it is the only
thing here that writes new facts; every REST endpoint reads or triggers deterministically.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .alerts import publish_alert
from .deps import AppState, build_state
from .router_actions import router as actions_router
from .router_assets import router as assets_router
from .router_overview import router as overview_router
from .router_reports import router as reports_router
from .router_runlog import router as runlog_router
from .security import new_secret, token_for, verify_token

_DIST = Path(__file__).resolve().parent.parent.parent / "webui" / "dist"


async def _scheduler_loop(state: AppState) -> None:
    """Periodically run one analysis window per asset, broadcast + alert.

    Mirrors cli._cmd_serve but bus-aware: each fresh window is announced on the
    event bus (so Monitor pages update live) and fed through alert derivation
    (so alerts fire only on real state transitions).
    """
    from ..core.bus import make_snapshot_event
    while True:
        for asset in state.config.assets:
            try:
                snap = await state.supervisor.run_once(asset)
                if not isinstance(snap, dict) or snap.get("error"):
                    continue
                try:
                    await state.bus.publish(
                        "snapshot", asset.asset_id,
                        payload=make_snapshot_event(asset.asset_id, snap),
                    )
                except Exception:  # noqa: BLE001
                    pass
                await publish_alert(state, asset.asset_id, snap)
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(state.config.monitor_interval_s)


def create_app(config_path: str | Path, gateway: Any | None = None) -> FastAPI:
    state = build_state(config_path, gateway=gateway)
    secret = new_secret()
    pin = state.config.hmi_pin
    pin_enabled = pin not in ("", "none", None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await state.start()
        loop_task = asyncio.create_task(_scheduler_loop(state))
        try:
            yield
        finally:
            loop_task.cancel()
            try:
                await loop_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            await state.stop()

    app = FastAPI(title="EdgeAdviser HMI", lifespan=lifespan)
    app.state.edge = state

    # --- optional shared PIN gate ---------------------------------------
    def check_auth(auth_value: str | None) -> JSONResponse | None:
        """Return an error response if the request lacks a valid token, else None."""
        if not pin_enabled:
            return None
        if auth_value and verify_token(auth_value, pin, secret):
            return None
        return JSONResponse({"ok": False, "data": None, "error": "unauthorized"},
                            status_code=401)

    app.include_router(assets_router)
    app.include_router(reports_router)
    app.include_router(runlog_router)
    app.include_router(overview_router)
    app.include_router(actions_router)

    # --- PIN exchange ---------------------------------------------------
    from pydantic import BaseModel

    class PinIn(BaseModel):
        pin: str

    @app.post("/api/auth/pin")
    async def pin_login(body: PinIn) -> Any:
        if not pin_enabled:
            return JSONResponse({"ok": True, "data": {"enabled": False,
                                                      "token": ""}})
        if body.pin == pin:
            return JSONResponse({
                "ok": True,
                "data": {"enabled": True, "token": token_for(pin, secret)},
            })
        return JSONResponse({"ok": False, "data": None, "error": "bad pin"},
                            status_code=401)

    # --- static SPA (only when built) -----------------------------------
    dist = _DIST
    if dist.exists():
        app.mount("/dist", StaticFiles(directory=dist, html=True), name="dist")

        @app.get("/")
        async def root() -> FileResponse:
            return FileResponse(dist / "index.html")
    else:
        @app.get("/")
        async def root():
            return JSONResponse({
                "ok": True,
                "data": {
                    "hint": "frontend not built; run `cd webui && npm install && npm run build`",
                    "dist_expected": str(dist),
                },
            })

    return app


# uvicorn target: build the app lazily using an env-var config path.
def app_factory() -> FastAPI:
    cfg = os.environ.get("EDGE_ADVISER_CONFIG", "config.sample.json")
    return create_app(cfg)


app = app_factory  # exposes an object for `uvicorn edge_adviser.server:app`