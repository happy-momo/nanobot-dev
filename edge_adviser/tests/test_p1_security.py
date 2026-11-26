"""P1 security: PIN attempt observability + optional TrustedHost gate.

A shared 4-6 digit PIN on 0.0.0.0 is brute-forceable; the limiter already
locks out repeat offenders, but an operator had no way to SEE the attempts.
/api/version now carries the counters (same endpoint the HMI already polls),
and `trusted_hosts` optionally pins the Host header allowlist.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.server.security import PinRateLimiter  # noqa: E402


def test_total_failures_survives_window_expiry() -> None:
    limiter = PinRateLimiter(max_failures=2, window_s=100, lockout_s=1)
    limiter.record_failure(now=1.0)
    limiter.record_failure(now=500.0)  # first failure aged out of the window
    assert limiter.total_failures == 2


def test_success_does_not_reset_total() -> None:
    limiter = PinRateLimiter()
    limiter.record_failure(now=1.0)
    limiter.record_success()
    assert limiter.total_failures == 1


class _Stub:
    """Minimal gateway stub (same shape test_server.py uses)."""
    async def astart(self): pass
    async def aclose(self): pass
    async def run(self, prompt, **kw): return {}


def _cfg(tmp: Path, *, trusted: list[str] | None = None,
         pin: str = "none") -> Path:
    cfg = {"workspace": str(tmp / "ws"), "hmi_pin": pin,
           "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                       "sample_freq_s": 1.0,
                       "metrics": [{"key": "temp", "unit": "degC",
                                    "norm_max": 68, "persist": 5}]}],
           "sources": [{"kind": "simulator", "asset_id": "pump1"}]}
    if trusted is not None:
        cfg["trusted_hosts"] = trusted
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_version_exposes_pin_attempt_counters(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    client = TestClient(create_app(_cfg(tmp_path), gateway=_Stub()))
    r = client.get("/api/version")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["pin_failed_attempts"] == 0
    assert data["pin_lockout_s"] == 0

    # a wrong PIN bumps the cumulative counter
    pindir = tmp_path / "pin"
    pindir.mkdir()
    cfg = _cfg(pindir, pin="1357")
    client2 = TestClient(create_app(cfg, gateway=_Stub()))
    assert client2.post("/api/auth/pin", json={"pin": "0000"}).status_code == 401
    v = client2.get("/api/version").json()["data"]
    assert v["pin_failed_attempts"] == 1


def test_trusted_host_rejects_foreign_host(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    # starlette strips the port from the request Host before matching, so the
    # pattern is a bare hostname (a ":8000" pattern would never match)
    client = TestClient(create_app(_cfg(tmp_path, trusted=["plant.local"]),
                                   gateway=_Stub()))
    ok = client.get("/api/version", headers={"Host": "plant.local:8000"})
    bad = client.get("/api/version", headers={"Host": "evil.example"})
    assert ok.status_code == 200
    assert bad.status_code == 400


def test_empty_trusted_hosts_allows_any(tmp_path: Path) -> None:
    """Default ([] = disabled) keeps today's behavior — the LAN is the boundary."""
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    client = TestClient(create_app(_cfg(tmp_path), gateway=_Stub()))
    assert client.get("/api/version", headers={"Host": "anything.local"}).status_code == 200
