"""P0 config-PATCH guardrails: backup + int type preservation.

The threshold PATCH is the only endpoint that writes the live config file; a
bad band edited at midnight must be one `cp` away from recovery, and an int
input (68) must not drift to 68.0 in the JSON (a walkthrough regression that
rewrote every future diff as a type change).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.server.router_assets import _backup_config  # noqa: E402


def test_backup_creates_timestamped_copy(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text('{"assets": []}', encoding="utf-8")
    bak = _backup_config(cfg)
    assert bak is not None and bak.exists()
    assert bak.name.startswith("config.json.bak-")
    assert json.loads(bak.read_text(encoding="utf-8")) == {"assets": []}


def test_backup_keeps_only_five(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    for _ in range(7):
        _backup_config(cfg)
    baks = sorted(tmp_path.glob("config.json.bak-*"))
    assert len(baks) == 5


def test_backup_returns_none_when_missing(tmp_path: Path) -> None:
    assert _backup_config(tmp_path / "nope.json") is None


class _Stub:
    """Minimal gateway stub (same shape test_server.py uses)."""
    async def astart(self): pass
    async def aclose(self): pass
    async def run(self, prompt, **kw): return {}


def _thresholds_config(tmp: Path) -> Path:
    metric = {"key": "temp", "unit": "degC", "norm_max": 68,
              "warn_max": 90, "crit_max": 95, "persist": 5, "min_samples": 30}
    cfg = {"workspace": str(tmp / "ws"), "hmi_pin": "none",
           "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                       "sample_freq_s": 1.0, "metrics": [metric]}],
           "sources": [{"kind": "simulator", "asset_id": "pump1"}]}
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_patch_preserves_int_type(tmp_path: Path) -> None:
    """68 → 68.0 drifted int to float in the demo walkthrough; the PATCH must
    write back the same JSON type the operator's value implies."""
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    cfg = _thresholds_config(tmp_path)
    client = TestClient(create_app(cfg, gateway=_Stub()))
    r = client.patch("/api/assets/pump1/metrics/temp/thresholds",
                     json={"warn_max": 68})
    assert r.status_code == 200, r.text
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    m = raw["assets"][0]["metrics"][0]
    assert m["warn_max"] == 68
    assert isinstance(m["warn_max"], int), f"int drifted to {type(m['warn_max'])}"
    # and a real fractional value still lands as float
    r2 = client.patch("/api/assets/pump1/metrics/temp/thresholds",
                      json={"warn_max": 68.5})
    assert r2.status_code == 200
    m2 = json.loads(cfg.read_text(encoding="utf-8"))["assets"][0]["metrics"][0]
    assert m2["warn_max"] == 68.5 and isinstance(m2["warn_max"], float)


def test_patch_creates_backup_before_write(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    cfg = _thresholds_config(tmp_path)
    client = TestClient(create_app(cfg, gateway=_Stub()))
    r = client.patch("/api/assets/pump1/metrics/temp/thresholds",
                     json={"warn_max": 85})
    assert r.status_code == 200
    baks = list(tmp_path.glob("config.json.bak-*"))
    assert len(baks) == 1, "PATCH must snapshot the config aside before writing"
    old = json.loads(baks[0].read_text(encoding="utf-8"))
    assert old["assets"][0]["metrics"][0]["warn_max"] == 90, \
        "backup holds the PRE-edit value"


def test_noop_patch_writes_no_backup(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    cfg = _thresholds_config(tmp_path)
    client = TestClient(create_app(cfg, gateway=_Stub()))
    r = client.patch("/api/assets/pump1/metrics/temp/thresholds",
                     json={"warn_max": 90})  # same as current → no changes
    assert r.status_code == 200
    assert not list(tmp_path.glob("config.json.bak-*")), \
        "a no-op edit must not churn backups"
