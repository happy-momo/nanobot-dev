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

import mimetypes

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# On Windows (and some minimal conda-forge Python builds) the platform MIME
# registry maps ".js" (and other frontend asset types) to text/plain. Starlette's
# StaticFiles then serves the built SPA's ES modules with Content-Type:
# text/plain and the browser refuses them ("Failed to load module script ...
# MIME type text/plain"). Force the correct types here — unconditionally, since
# the platform default is wrong, not merely missing. Note: the registry is
# process-global and never re-initialized in a normally-running uvicorn process.
_WEB_MIME: dict[str, str] = {
    ".js": "application/javascript",
    ".mjs": "text/javascript",
    ".cjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".wasm": "application/wasm",
    ".map": "application/json",
    ".txt": "text/plain",
}
for _ext, _mime in _WEB_MIME.items():
    mimetypes.add_type(_mime, _ext)
del _ext, _mime

from .alerts import publish_alert
from .deps import AppState, build_state
from .router_actions import router as actions_router
from .router_assets import router as assets_router
from .router_overview import router as overview_router
from .router_reports import router as reports_router
from .router_runlog import router as runlog_router
from .security import new_secret, token_for, verify_token

_DIST = Path(__file__).resolve().parent.parent.parent / "webui" / "dist"

# Module-level so FastAPI can resolve the annotation under
# `from __future__ import annotations` (a class defined inside create_app is
# only visible as a string name and the body param silently becomes required
# query arg → 422 on every PIN exchange).
from pydantic import BaseModel


class PinIn(BaseModel):
    pin: str


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


def create_app(config_path: str | Path, gateway: Any | None = None,
               demo: bool | None = None) -> FastAPI:
    state = build_state(config_path, gateway=gateway)
    if demo is not None:
        state.config.demo = bool(demo)
    secret = new_secret()
    pin = state.config.hmi_pin
    pin_enabled = pin not in ("", "none", None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await state.start()
        # Demo mode: seed a rich workspace (snapshots/reports/alerts/audit) so
        # every page has data on first load, before the scheduler starts writing.
        if state.config.demo:
            from .demo import seed_demo_workspace
            await seed_demo_workspace(state)
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

    if pin_enabled:
        # Middleware (not per-route Depends) so every current and future /api and
        # /stream endpoint is covered without each router remembering to opt in.
        # Exempt: the PIN exchange itself, health/version probes, and anything
        # outside the API/SSE surface (the SPA + /dist assets). `?token=` is
        # accepted because EventSource cannot send an Authorization header.
        _EXEMPT_PATHS = {"/api/health", "/api/version"}

        @app.middleware("http")
        async def pin_gate(request: Request, call_next):
            p = request.url.path
            protected = p.startswith("/api") or p.startswith("/stream")
            exempt = (not protected
                      or p.startswith("/api/auth/pin")
                      or p in _EXEMPT_PATHS)
            if not exempt:
                auth = request.headers.get("authorization", "")
                token = auth[7:] if auth.lower().startswith("bearer ") else ""
                if not token:
                    token = request.query_params.get("token", "")
                resp = check_auth(token)
                if resp is not None:
                    return resp
            return await call_next(request)

    app.include_router(assets_router)
    app.include_router(reports_router)
    app.include_router(runlog_router)
    app.include_router(overview_router)
    app.include_router(actions_router)

    # --- PIN exchange ---------------------------------------------------
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

        # SPA deep-link fallback: a history-mode route like /dist/monitor/pump1
        # matches no file on disk (StaticFiles html=True only maps physical
        # <dir>/index.html), so extension-less /dist/* misses are rewritten to the
        # app shell and the router takes over. Real asset misses still 404.
        @app.exception_handler(404)
        async def spa_fallback(request: Request, exc: Any) -> Any:
            if request.url.path.startswith("/dist/") and \
                    "." not in Path(request.url.path).name:
                return FileResponse(dist / "index.html",
                                    headers={"Cache-Control": "no-cache"})
            return JSONResponse({"ok": False, "data": None, "error": "not found"},
                                status_code=404)

        @app.get("/")
        async def root() -> FileResponse:
            # Never cache index.html: its chunk hashes change on every rebuild, and
            # a stale cache referencing deleted chunks cascades into 404s for every
            # lazy-loaded route. The hashed assets themselves keep long caching.
            return FileResponse(dist / "index.html",
                                headers={"Cache-Control": "no-cache"})
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