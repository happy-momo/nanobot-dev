"""EdgeAdviser CLI — operate the edge deployment from a terminal.

Commands:
    edge-adviser setup <config>          validate config & materialize ASSET.md
    edge-adviser run <config> --once     pull+analyze+archive one window
    edge-adviser monitor <config>        routine local-model status for all assets
    edge-adviser report <config> <asset> deep report (online, degrades to local)
    edge-adviser learn <config> [asset]  run the historical-learning pass
    edge-adviser ask <config> <asset> "<question>"   Q&A style status query
    edge-adviser feedback <config> <asset> "<note>"  record operator feedback
    edge-adviser serve <config>          run the long-lived supervisor loop
    edge-adviser web <config> [--host H] [--port P]   serve the B/S HMI (FastAPI)

Uses the same nanobot config/workspace wiring as the SDK.

`web` serves the human-machine interface: a FastAPI backend (REST /api + SSE
/stream) plus the built Vue SPA. Build the frontend first:
    cd edge_adviser/webui && npm install && npm run build
Then `edge-adviser web <config>` (default http://0.0.0.0:8000/).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import load_config
from .core import AssetStore
from .core.asset_store import create_asset_memory_store  # reused pattern
from .core.scheduler import Supervisor
from .ingest import create_source
from .learn.feedback import FeedbackService
from .learn.learner import Learner
from .llm.context import ContextBuilder
from .llm.gateway import LLMGateway
from .qa.engine import QAEngine
from .report.generator import ReportGenerator


def _build(config_path: str | Path) -> "Context":
    config = load_config(config_path)
    store = AssetStore(config.workspace)
    for asset in config.assets:
        store.ensure_asset(asset)
    ctx = ContextBuilder(config, store)
    gateway = LLMGateway(config, ctx)
    report_gen = ReportGenerator(config, store, gateway)
    learner = Learner(config, store, gateway)
    qa = QAEngine(config, store, gateway)
    feedback = FeedbackService(store)
    return _Context(config, store, gateway, report_gen, learner, qa, feedback)


class _Context:
    def __init__(self, config, store, gateway, report_gen, learner, qa, feedback):
        self.config = config
        self.store = store
        self.gateway = gateway
        self.report_gen = report_gen
        self.learner = learner
        self.qa = qa
        self.feedback = feedback


async def _cmd_setup(c: _Context) -> None:
    for asset in c.config.assets:
        p = c.store.ensure_asset(asset)
        print(f"[setup] {asset.asset_id}: ASSET.md -> {p}")
    print(f"[setup] workspace: {c.config.workspace}")


async def _cmd_run_once(c: _Context, asset_id: str) -> None:
    asset = c.config.asset(asset_id)
    if asset is None:
        raise SystemExit(f"unknown asset: {asset_id}")
    src = _make_source(c, asset_id)
    snap = await _supervisor(c).run_once(asset, src)
    print(snap)


def _make_source(c: _Context, asset_id: str):
    from .config.model import SourceSpec
    spec = next((s for s in c.config.sources if s.asset_id == asset_id), None)
    if spec is None:
        raise SystemExit(f"no source for asset {asset_id}")
    try:
        return create_source(spec)
    except NotImplementedError as exc:
        raise SystemExit(f"source not available: {exc}") from None


def _supervisor(c: _Context) -> Supervisor:
    return Supervisor(
        config=c.config, store=c.store, gateway=c.gateway,
        report_gen=c.report_gen, learner=c.learner,
        # N9: pass the configured analysis-regime label (no hardcoded "degrade").
        default_regime=c.config.analysis_regime,
    )


async def _cmd_monitor(c: _Context, asset_id: str) -> None:
    asset = c.config.asset(asset_id)
    if asset is None:
        raise SystemExit(f"unknown asset: {asset_id}")
    src = _make_source(c, asset_id)
    sup = _supervisor(c)
    await sup.gateway.astart()
    try:
        await sup.run_once(asset, src)
        res = await sup.run_monitor(asset)
        print(res.content or res.error or "(empty)")
    finally:
        await sup.stop()


async def _cmd_report(c: _Context, asset_id: str) -> None:
    asset = c.config.asset(asset_id)
    sup = _supervisor(c)
    await sup.gateway.astart()
    try:
        res = await sup.run_deep_report(asset)
        print(json_dumps(res))
    finally:
        await sup.stop()


async def _cmd_learn(c: _Context, asset_id: str | None) -> None:
    sup = _supervisor(c)
    await sup.gateway.astart()
    try:
        targets = [asset for asset in c.config.assets
                   if asset_id is None or asset.asset_id == asset_id]
        for asset in targets:
            res = await sup.run_learn(asset)
            print(f"[learn] {asset.asset_id}: {res.content[:200].strip() or res.error}")
    finally:
        await sup.stop()


async def _cmd_ask(c: _Context, asset_id: str, question: str) -> None:
    asset = c.config.asset(asset_id)
    await c.gateway.astart()
    try:
        res = await c.qa.ask(asset, question)
        print(res.content or res.error or "(empty)")
    finally:
        await c.gateway.aclose()


async def _cmd_feedback(c: _Context, asset_id: str, note: str) -> None:
    c.feedback.record(asset_id, note)
    print(f"[feedback] recorded for {asset_id}")


async def _cmd_serve(c: _Context) -> None:
    sup = _supervisor(c)
    # simple loop: each asset every monitor_interval_s, deep report daily
    import time
    await sup.start()
    last_deep: dict[str, float] = {}
    try:
        while True:
            for asset in c.config.assets:
                try:
                    snap = await sup.run_once(asset)
                except Exception as exc:
                    print(f"[serve] run err {asset.asset_id}: {exc}")
                    continue
                # P4: surface (rather than discard) a failed/empty window so the
                # operator sees why a report may be missing.
                if isinstance(snap, dict) and snap.get("error"):
                    print(f"[serve] {asset.asset_id}: run not OK — {snap.get('error')} "
                          f"(state={snap.get('status', snap.get('error'))})")
                now = time.time()
                if now - last_deep.get(asset.asset_id, 0) >= 86400:
                    try:
                        await sup.run_deep_report(asset)
                    except Exception as exc:
                        print(f"[serve] deep report err {asset.asset_id}: {exc}")
                    last_deep[asset.asset_id] = now
            await asyncio.sleep(c.config.monitor_interval_s)
    finally:
        await sup.stop()


async def _cmd_web(c: _Context, host: str | None, port: int | None) -> int:
    """Serve the B/S human-machine interface (FastAPI + built SPA).

    Rebuild the frontend first: `cd edge_adviser/webui && npm install && npm run build`.
    Otherwise the API is served and `/` returns a hint pointing at the build dir.
    """
    import uvicorn
    from .server.app import create_app, _DIST

    app = create_app(c.config.defaults.get("_source_file", "config.json"))
    bind_host = host or getattr(c.config, "hmi_host", "0.0.0.0")
    bind_port = port or getattr(c.config, "hmi_port", 8000)
    if not _DIST.exists():
        print(f"[web] frontend not built; expected at {_DIST}")
        print("[web]   cd edge_adviser/webui && npm install && npm run build")
    print(f"[web] EdgeAdviser HMI -> http://{bind_host}:{bind_port}/  "
          f"(api=/api, sse=/stream)")
    cfg = uvicorn.Config(app, host=bind_host, port=bind_port, log_level="info")
    server = uvicorn.Server(cfg)
    await server.serve()
    return 0


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge-adviser", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add(cmd, help_):
        p = sub.add_parser(cmd, help=help_)
        p.add_argument("config", type=Path)
        return p

    add("setup", "validate config & materialize ASSET.md").add_argument("--noop", action="store_true")
    p = add("run", "pull+analyze+archive one window"); p.add_argument("asset")
    p = add("monitor", "routine status"); p.add_argument("asset")
    p = add("report", "deep report"); p.add_argument("asset")
    p = add("learn", "learn pass"); p.add_argument("asset", nargs="?")
    p = add("ask", "Q&A"); p.add_argument("asset"); p.add_argument("question")
    p = add("feedback", "record feedback"); p.add_argument("asset"); p.add_argument("note")
    add("serve", "run supervisor loop")
    pw = add("web", "serve the B/S human-machine interface (FastAPI)")
    pw.add_argument("--host", default=None)
    pw.add_argument("--port", type=int, default=None)

    args = parser.parse_args(argv)
    c = _build(args.config)

    async def _dispatch() -> int:
        if args.cmd == "setup":
            await _cmd_setup(c)
        elif args.cmd == "run":
            await _cmd_run_once(c, args.asset)
        elif args.cmd == "monitor":
            await _cmd_monitor(c, args.asset)
        elif args.cmd == "report":
            await _cmd_report(c, args.asset)
        elif args.cmd == "learn":
            await _cmd_learn(c, getattr(args, "asset", None))
        elif args.cmd == "ask":
            await _cmd_ask(c, args.asset, args.question)
        elif args.cmd == "feedback":
            await _cmd_feedback(c, args.asset, args.note)
        elif args.cmd == "serve":
            await _cmd_serve(c)
        elif args.cmd == "web":
            return await _cmd_web(c, host=args.host, port=args.port)
        return 0

    try:
        return asyncio.run(_dispatch())
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())