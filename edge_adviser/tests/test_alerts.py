"""Alert derivation/dedup — the deterministic part of the HMI alert loop.

Verifies the two facts that warrant an alert (real alarm level OR data fault),
the exemptions (data-coverage verdicts + planned downtime), and the dedup rule
(one active alert per asset+subject until acknowledged).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

import edge_adviser.server.alerts as alerts  # noqa: E402


def _snap(*, status="healthy", level="warn") -> dict:
    return {
        "status": status,
        "overall_level": level,
        "state_reason": "vibration approaching warn band",
        "headline": "some parameters approach warning bands.",
        "window_end": "2026-11-18T00:00:00Z",
    }


def test_real_alarm_warn_as_healthy_status() -> None:
    """A warn-level metric with a 'healthy' data status MUST still alert (subject=warn)."""
    a = alerts.derive_alert("pump1", _snap(status="healthy", level="warn"))
    assert a is not None
    assert a["subject"] == "warn"
    assert a["state"] == "healthy"


def test_critical_alerts() -> None:
    a = alerts.derive_alert("pump1", _snap(status="healthy", level="critical"))
    assert a is not None and a["subject"] == "critical"


def test_data_fault_offline_alerts_without_alarm() -> None:
    """Offline (data fault) must alert even when level is not warn/critical."""
    a = alerts.derive_alert("pump1", _snap(status="offline", level="info"))
    assert a is not None and a["subject"] == "offline"


def test_data_coverage_verdicts_do_not_alert() -> None:
    """unknown / insufficient / planned_downtime are informational, not alarms."""
    for st in ("unknown", "insufficient", "planned_downtime"):
        assert alerts.derive_alert("pump1", _snap(status=st)) is None


def test_healthy_info_does_not_alert() -> None:
    assert alerts.derive_alert("pump1", _snap(status="healthy", level="info")) is None


@pytest.mark.asyncio
async def test_dedup_one_active_alert_per_subject(tmp_path: Path) -> None:
    """Repeated warn snapshots produce one alert until acknowledged."""
    from edge_adviser.server.deps import AppState
    from edge_adviser.core.asset_store import AssetStore
    from edge_adviser.config.model import AdviserConfig

    store = AssetStore(tmp_path / "ws")
    cfg = AdviserConfig(workspace=tmp_path / "ws")
    state = AppState(cfg, store, gateway=type("G", (), {
        "astart": lambda: None, "aclose": lambda: None,
    })())

    first = alerts.ingest_alert(state, "pump1", _snap(level="warn"))
    assert first is not None
    second = alerts.ingest_alert(state, "pump1", _snap(level="warn"))  # same subject + unacked
    assert second is None  # dedup
    # different subject fires a new alert
    third = alerts.ingest_alert(state, "pump1", _snap(status="offline", level="info"))
    assert third is not None and third["subject"] == "offline"

    persisted = alerts.list_alerts(state, "pump1")
    assert len(persisted) == 2

    # ack the warn -> frees the key so a new warn alerts again
    assert await alerts.acknowledge(state, "pump1", first["ts"]) is True
    fresh = alerts.ingest_alert(state, "pump1", _snap(level="warn"))
    assert fresh is not None and fresh["subject"] == "warn"


@pytest.mark.asyncio
async def test_acknowledge_sets_acked(tmp_path: Path) -> None:
    from edge_adviser.server.deps import AppState
    from edge_adviser.core.asset_store import AssetStore
    from edge_adviser.config.model import AdviserConfig

    store = AssetStore(tmp_path / "ws")
    cfg = AdviserConfig(workspace=tmp_path / "ws")
    state = AppState(cfg, store, gateway=object())
    a = alerts.ingest_alert(state, "pump1", _snap(level="warn"))
    assert a is not None
    await alerts.acknowledge(state, "pump1", a["ts"])
    recs = alerts.list_alerts(state, "pump1", include_acked=True)
    assert recs[0]["acked"] is True