"""Regression tests for the 0.4.1 third batch (usability + security fixes).

  * /api/assets/{id}/snapshots returns oldest-first (charts read left→right),
  * report archives persist a conclusion so the learn loop sees the previous
    report's takeaway,
  * the HMAC secret persists across restarts (tokens stay valid),
  * the PIN exchange locks out after repeated failures,
  * a failed write action reports phase=failed on the task stream,
  * demo downtime windows are re-injected on a same-day restart.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ensure the inner package is importable regardless of CWD
_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import (  # noqa: E402
    AdviserConfig,
    AssetSpec,
    MetricSpec,
)
from edge_adviser.core import AssetStore  # noqa: E402


def make_asset(asset_id: str = "pump1") -> AssetSpec:
    return AssetSpec(
        asset_id=asset_id,
        name="Test Pump",
        window_s=60,
        sample_freq_s=1.0,
        metrics=[MetricSpec(key="temp", unit="degC")],
    )


# --------------------------------------------------------------------------- #
# snapshots endpoint: oldest-first for charts
# --------------------------------------------------------------------------- #
def test_snapshots_endpoint_returns_oldest_first(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.deps import build_state
    from edge_adviser.server.app import create_app

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "workspace": str(tmp_path / "ws"),
        "assets": [{"asset_id": "pump1", "name": "Test Pump", "window_s": 60,
                    "sample_freq_s": 1.0,
                    "metrics": [{"key": "temp", "unit": "degC"}]}],
    }), encoding="utf-8")

    class _Gw:
        async def astart(self):
            pass

        async def aclose(self):
            pass

        async def run(self, *a, **k):
            from edge_adviser.llm.gateway import LLMResult
            return LLMResult(content="")

    # seed snapshots out of order to prove sorting, not insertion order
    store = AssetStore(tmp_path / "ws")
    for ts in ["10:00:03", "10:00:01", "10:00:02"]:
        store.save_snapshot("pump1", {"window_end": f"2026-11-19T{ts}+00:00",
                                      "sample_count": 1})
    app = create_app(cfg, gateway=_Gw())
    c = TestClient(app)
    rows = c.get("/api/assets/pump1/snapshots").json()["data"]
    ends = [r["window_end"] for r in rows]
    assert ends == sorted(ends), "charts expect oldest-first"


# --------------------------------------------------------------------------- #
# conclusion chain: archive persists it, learner reads it
# --------------------------------------------------------------------------- #
def test_archive_persists_conclusion(tmp_path: Path) -> None:
    from edge_adviser.report.generator import ReportGenerator

    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])

    class _Stub:
        async def run(self, *a, **k):
            from edge_adviser.llm.gateway import LLMResult
            return LLMResult(content="")

    gen = ReportGenerator(config, store, _Stub())  # type: ignore
    report_md = ("# Report\n\n## Executive Summary\n"
                 "Bearing temperature remains nominal; continue monitoring.\n")
    out = gen.archive(asset, {"asset_id": "pump1"}, report_md)
    doc = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    assert "nominal" in doc.get("conclusion", "")


def test_learner_prev_conclusion_reads_archive(tmp_path: Path) -> None:
    from edge_adviser.report.generator import ReportGenerator
    from edge_adviser.learn.learner import Learner

    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])

    class _Stub:
        async def run(self, *a, **k):
            from edge_adviser.llm.gateway import LLMResult
            return LLMResult(content="")

    gen = ReportGenerator(config, store, _Stub())  # type: ignore
    gen.archive(asset, {"asset_id": "pump1"},
                "# R\n\n## Executive Summary\nPump vibration trending upward.\n")
    learner = Learner(config, store, _Stub())  # type: ignore
    assert "vibration" in learner._prev_conclusion("pump1")


def test_learner_prev_conclusion_falls_back_to_report_md(tmp_path: Path) -> None:
    """Archives written before the conclusion field existed still resolve."""
    from edge_adviser.learn.learner import Learner

    store = AssetStore(tmp_path)
    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])

    class _Stub:
        async def run(self, *a, **k):
            from edge_adviser.llm.gateway import LLMResult
            return LLMResult(content="")

    day_dir = store.reports_dir("pump1") / "2026-11-01"
    day_dir.mkdir(parents=True)
    (day_dir / "snapshot.json").write_text(json.dumps({"meta": {}}), encoding="utf-8")
    (day_dir / "report.md").write_text(
        "# R\n\n## Executive Summary\nLegacy archive conclusion text.\n", encoding="utf-8")
    learner = Learner(config, store, _Stub())  # type: ignore
    assert "Legacy archive" in learner._prev_conclusion("pump1")


# --------------------------------------------------------------------------- #
# secret persistence + PIN lockout
# --------------------------------------------------------------------------- #
def test_secret_persists_across_calls(tmp_path: Path) -> None:
    from edge_adviser.server.security import load_or_create_secret

    f = tmp_path / "ws" / ".hmi_secret"
    s1 = load_or_create_secret(f)
    s2 = load_or_create_secret(f)
    assert s1 == s2 and s1
    assert f.exists()


def test_tokens_survive_restart(tmp_path: Path) -> None:
    """A token issued under the persisted secret verifies after 'restart'."""
    from edge_adviser.server.security import load_or_create_secret, token_for, verify_token

    f = tmp_path / ".hmi_secret"
    secret = load_or_create_secret(f)
    tok = token_for("1234", secret)
    # simulate a fresh process loading the same secret file
    secret2 = load_or_create_secret(f)
    assert verify_token(tok, "1234", secret2)


def test_pin_lockout_after_repeated_failures(tmp_path: Path) -> None:
    from edge_adviser.server.security import PinRateLimiter

    lim = PinRateLimiter(max_failures=3, window_s=60, lockout_s=120)
    assert lim.locked() == 0.0
    lim.record_failure()
    lim.record_failure()
    assert lim.locked() == 0.0  # below threshold
    lim.record_failure()
    assert lim.locked() > 0.0   # now locked
    lim.record_success()
    assert lim.locked() == 0.0  # success clears


def test_pin_endpoint_locks_out(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "workspace": str(tmp_path / "ws"),
        "hmi_pin": "1234",
        "assets": [{"asset_id": "pump1", "name": "t", "window_s": 60,
                    "sample_freq_s": 1.0,
                    "metrics": [{"key": "temp", "unit": "degC"}]}],
    }), encoding="utf-8")

    class _Gw:
        async def astart(self):
            pass

        async def aclose(self):
            pass

        async def run(self, *a, **k):
            from edge_adviser.llm.gateway import LLMResult
            return LLMResult(content="")

    app = create_app(cfg, gateway=_Gw())
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/pin", json={"pin": "0000"}).status_code == 401
    # locked now — even the CORRECT pin is refused with 429
    r = c.post("/api/auth/pin", json={"pin": "1234"})
    assert r.status_code == 429


# --------------------------------------------------------------------------- #
# task stream: failed actions report phase=failed
# --------------------------------------------------------------------------- #
def test_task_mark_failed_drives_phase(tmp_path: Path) -> None:
    from edge_adviser.server.router_actions import _task
    from edge_adviser.core.bus import EventBus

    bus = EventBus()
    published: list[dict] = []

    async def relay(ev: dict) -> None:
        published.append(ev)

    class _State:  # minimal AppState stand-in
        pass
    _State.bus = bus

    async def scenario():
        sub = await bus.subscribe({"task"}, None, include_replay=False, on_event=relay)
        async with _task(_State(), "pump1", "report") as t:
            t.mark_failed()
            # handler returns fail() — no exception raised
        await bus.unsubscribe(relay)

    import asyncio
    asyncio.run(scenario())
    phases = [p["payload"]["phase"] for p in published]
    assert phases == ["start", "failed"], "return-fail() must surface as failed"


def test_task_success_reports_done(tmp_path: Path) -> None:
    from edge_adviser.server.router_actions import _task
    from edge_adviser.core.bus import EventBus

    bus = EventBus()
    published: list[dict] = []

    async def relay(ev: dict) -> None:
        published.append(ev)

    class _State:
        pass
    _State.bus = bus

    async def scenario():
        sub = await bus.subscribe({"task"}, None, include_replay=False, on_event=relay)
        async with _task(_State(), "pump1", "run-now") as t:
            pass  # handler returns ok()
        await bus.unsubscribe(relay)

    import asyncio
    asyncio.run(scenario())
    phases = [p["payload"]["phase"] for p in published]
    assert phases == ["start", "done"]


# --------------------------------------------------------------------------- #
# demo downtime re-injection on same-day restart
# --------------------------------------------------------------------------- #
def test_demo_downtime_injected_before_seeded_today(tmp_path: Path) -> None:
    """Even when already seeded today, tower1 gets a live downtime window."""
    from edge_adviser.server.demo import seed_demo_workspace, MARKER

    class _Cfg:
        workspace = tmp_path
        assets = [make_asset("tower1")]
        demo = True

    class _Store:
        pass

    class _State:
        config = _Cfg()
        store = _Store()

    marker = tmp_path / MARKER
    marker.write_text(json.dumps({"date": datetime.now(timezone.utc)
                                  .strftime("%Y-%m-%d"), "version": 1}),
                      encoding="utf-8")
    # seeded today → the full seed body is skipped, but the window is set
    import asyncio
    asyncio.run(seed_demo_workspace(_State()))
    win = _State.config.assets[0].downtime_windows[0]
    now = datetime.now(timezone.utc)
    start = datetime.fromisoformat(win["start"])
    end = datetime.fromisoformat(win["end"])
    assert start < now < end, "downtime window must cover now after restart"
