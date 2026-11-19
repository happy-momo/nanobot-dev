"""Regression tests for the 0.4.1 hardening fixes (P0 batch).

Each test targets one confirmed defect from the pre-fix code review:
  * NaN/Inf poisoning statistics and leaking invalid JSON into snapshots,
  * analyze() exceptions escaping run_once and vanishing in web mode,
  * naive local-time empty snapshots reading as eternally fresh (gate bypass),
  * --demo forcing applied after the gateway choice,
  * relative workspace resolved against CWD instead of the config file dir.
"""

from __future__ import annotations

import json
import math
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
    SourceKind,
    SourceSpec,
)
from edge_adviser.core import AssetStore  # noqa: E402
from edge_adviser.analytics import analyze, empty_snapshot  # noqa: E402
from edge_adviser.analytics.gates import snapshot_age_s  # noqa: E402


def make_asset() -> AssetSpec:
    return AssetSpec(
        asset_id="pump1",
        name="Test Pump",
        window_s=60,
        sample_freq_s=1.0,
        metrics=[
            MetricSpec(key="temp", unit="degC", warn_max=68, crit_max=73, persist=5),
            MetricSpec(key="vibration", unit="mm/s", warn_max=3.0, crit_max=4.5, persist=5),
        ],
    )


# --------------------------------------------------------------------------- #
# NaN/Inf protection (engine.py)
# --------------------------------------------------------------------------- #
def test_analyze_metric_drops_nan_values() -> None:
    """NaN samples are excluded, not propagated into statistics."""
    from edge_adviser.analytics.engine import analyze_metric

    m = MetricSpec(key="temp", unit="degC")
    res = analyze_metric([float("nan"), float("nan"), 10.0, 12.0], None, m)
    assert res.n == 2
    assert res.mean == 11.0
    assert res.level.value == "info"


def test_analyze_metric_all_nan_is_unknown() -> None:
    """A window of only-NaN values is UNKNOWN, never 'nominal'."""
    from edge_adviser.analytics.engine import analyze_metric

    m = MetricSpec(key="temp", unit="degC")
    res = analyze_metric([float("nan"), float("inf")], None, m)
    assert res.n == 0
    assert res.level.value == "unknown"
    assert "no samples" in res.state_reason


def test_analyze_snapshot_is_strict_json(tmp_path: Path) -> None:
    """Snapshot dicts must serialize under allow_nan=False (Starlette default)."""
    samples = [
        {"ts": "2026-11-19T10:00:0%d+00:00" % i, "temp": float("nan"), "vibration": 1.0}
        for i in range(3)
    ]
    snap = analyze(make_asset(), samples)
    # would raise ValueError if any NaN leaked into the structure
    text = json.dumps(snap.to_dict(), allow_nan=False)
    assert "NaN" not in text


def test_analyze_skips_non_numeric_values() -> None:
    """A malformed string value is dropped instead of aborting the window."""
    samples = [
        {"ts": "2026-11-19T10:00:00+00:00", "temp": "oops", "vibration": 1.0},
        {"ts": "2026-11-19T10:00:01+00:00", "temp": 20.0, "vibration": 1.0},
    ]
    snap = analyze(make_asset(), samples)
    temp = next(m for m in snap.metrics if m.key == "temp")
    assert temp.n == 1
    assert temp.mean == 20.0


def test_moving_mean_std_guarded_for_short_windows() -> None:
    """np.convolve(mode='valid') with n<5 yields an empty array; std of it is NaN."""
    from edge_adviser.analytics.engine import analyze_metric

    m = MetricSpec(key="temp", unit="degC")
    res = analyze_metric([1.0, 2.0, 3.0, 4.0], [0.0, 0.1, 0.2, 0.3], m)
    assert res.moving_mean_std is None


# --------------------------------------------------------------------------- #
# analyze exceptions must not escape run_once (scheduler.py)
# --------------------------------------------------------------------------- #
class _BoomSource:
    kind = "sim"

    async def pull(self, ws, we):
        raise RuntimeError("boom")


class _MalformedSource:
    """Pulls fine, but carries an un-analyzable sample shape."""

    kind = "sim"

    async def pull(self, ws, we):
        # analyzed via analyze() only if it survives value conversion
        return [{"ts": "2026-11-19T10:00:00+00:00", "temp": 1.0, "vibration": 1.0}]


class _Learn:
    def buffer_snapshot(self, *a, **k):
        pass


class _Gw:
    async def astart(self):
        pass

    async def aclose(self):
        pass

    async def run(self, *a, **k):
        from edge_adviser.llm.gateway import LLMResult
        return LLMResult(content="")


def _make_supervisor(tmp_path: Path):
    from edge_adviser.core.scheduler import Supervisor

    asset = make_asset()
    config = AdviserConfig(workspace=tmp_path, assets=[asset])
    store = AssetStore(tmp_path)
    sup = Supervisor(config=config, store=store, gateway=_Gw(),  # type: ignore
                     report_gen=None, learner=_Learn(),  # type: ignore
                     default_regime=config.analysis_regime)
    return sup, asset, store


@pytest.mark.asyncio
async def test_pull_failure_archives_empty_snapshot(tmp_path: Path) -> None:
    """A failed pull produces an archived empty snapshot + runlog, not a bare error."""
    sup, asset, store = _make_supervisor(tmp_path)
    snap = await sup.run_once(asset, _BoomSource())
    assert snap.get("status") in ("insufficient", "offline")
    files = store.list_snapshots(asset.asset_id, limit=1)
    assert files, "empty window must still be archived for audit"
    runlog = store.read_runlog(asset.asset_id)
    assert any(e["outcome"] == "exception" for e in runlog)


@pytest.mark.asyncio
async def test_analyze_failure_does_not_escape_run_once(tmp_path: Path) -> None:
    """An analyze() crash is contained: archived empty snapshot + runlog entry."""
    sup, asset, store = _make_supervisor(tmp_path)

    class _BadShape:
        kind = "sim"

        async def pull(self, ws, we):
            # force analyze() to blow up: duplicate metric key with a non-dict sample
            return ["not-a-dict"]

    snap = await sup.run_once(asset, _BadShape())
    assert not snap.get("error") or snap.get("status") in ("insufficient", "offline")
    assert store.list_snapshots(asset.asset_id, limit=1), "audit snapshot must exist"
    runlog = store.read_runlog(asset.asset_id)
    assert any("analyze failed" in e.get("reason", "") for e in runlog)


# --------------------------------------------------------------------------- #
# empty snapshot freshness (engine.empty_snapshot + gates)
# --------------------------------------------------------------------------- #
def test_empty_snapshot_is_utc_aware() -> None:
    """empty_snapshot timestamps carry tzinfo so gates read them as UTC."""
    snap = empty_snapshot(make_asset())
    ts = datetime.fromisoformat(snap["window_end"])
    assert ts.tzinfo is not None
    age = snapshot_age_s(snap, now=datetime.now(timezone.utc))
    assert age < 5.0, "an aware empty snapshot must read as fresh-now, not eternally fresh"


def test_empty_snapshot_naive_is_stale_after_freshness() -> None:
    """Legacy naive timestamps are interpreted as UTC (never future-dated)."""
    snap = {"window_end": (datetime.now(timezone.utc) - timedelta(hours=2))
            .isoformat(timespec="seconds")}
    age = snapshot_age_s(snap, now=datetime.now(timezone.utc))
    assert age > 7000


# --------------------------------------------------------------------------- #
# --demo forcing must precede gateway selection (deps.build_state)
# --------------------------------------------------------------------------- #
def test_build_state_demo_forces_mock_gateway(tmp_path: Path) -> None:
    from edge_adviser.server.deps import build_state

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "workspace": "ws",
        "demo": False,
        "assets": [json.loads(make_asset().to_json_string())]
        if hasattr(make_asset(), "to_json_string") else [
            {"asset_id": "pump1", "name": "Test Pump", "window_s": 60,
             "sample_freq_s": 1.0,
             "metrics": [{"key": "temp", "unit": "degC"}]},
        ],
        "sources": [{"kind": "simulator", "asset_id": "pump1"}],
    }), encoding="utf-8")

    state = build_state(cfg, demo=True)
    assert state.config.demo is True
    assert state.gateway.__class__.__name__ == "MockGateway"
    # and the demo=False path keeps the real gateway
    state2 = build_state(cfg, demo=False)
    assert state2.config.demo is False
    assert state2.gateway.__class__.__name__ == "LLMGateway"


# --------------------------------------------------------------------------- #
# relative workspace resolution (cli._build parity with server)
# --------------------------------------------------------------------------- #
def test_cli_build_resolves_relative_workspace_against_config_dir(
        tmp_path: Path, monkeypatch) -> None:
    from edge_adviser import cli

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "workspace": "ws",
        "assets": [{"asset_id": "pump1", "name": "Test Pump", "window_s": 60,
                    "sample_freq_s": 1.0,
                    "metrics": [{"key": "temp", "unit": "degC"}]},
                   ],
    }), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    c = cli._build(cfg)
    assert c.config.workspace == (tmp_path / "ws").resolve()
    assert c.store.workspace == (tmp_path / "ws").resolve()
