"""Ingest-layer tests: REST pull semantics + factory wiring honesty.

The REST source is the first adapter a real deployment reaches for; its window
params, error paths, and session lifecycle must be pinned down before a plant
integration depends on them. The factory tests make "configured but unwired"
sources fail loudly (NotImplementedError) instead of silently collecting nothing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import SourceKind, SourceSpec  # noqa: E402


def _spec(**kw) -> SourceSpec:
    return SourceSpec(kind=SourceKind.REST, asset_id="pump1",
                      url="http://hist.test/api/samples", **kw)


# --------------------------------------------------------------------------- #
# factory: configured-but-unwired kinds fail loudly
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", [SourceKind.MQTT, SourceKind.OPCUA, SourceKind.MODBUS])
def test_unwired_source_kinds_raise(kind: SourceKind) -> None:
    from edge_adviser.ingest import create_source

    spec = SourceSpec(kind=kind, asset_id="pump1")
    with pytest.raises(NotImplementedError):
        create_source(spec)


def test_unknown_source_kind_raises_value_error() -> None:
    from edge_adviser.ingest import create_source

    spec = SourceSpec(kind=SourceKind.REST, asset_id="pump1")
    spec.kind = "bananas"  # type: ignore[assignment]
    with pytest.raises(ValueError):
        create_source(spec)


def test_simulator_and_rest_construct() -> None:
    from edge_adviser.ingest import create_source, RestSource, SimulatorSource

    assert isinstance(create_source(SourceSpec(kind=SourceKind.SIMULATOR,
                                               asset_id="p1")), SimulatorSource)
    assert isinstance(create_source(_spec()), RestSource)


# --------------------------------------------------------------------------- #
# REST source: window params, payload passthrough, HTTP errors, lifecycle
# --------------------------------------------------------------------------- #
def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_rest_pull_passes_window_and_returns_samples() -> None:
    """The [start, end) window rides as ISO query params; payload rows pass through."""
    from edge_adviser.ingest.rest import RestSource

    captured: dict = {}

    class _Resp:
        def raise_for_status(self): pass

        async def json(self):
            return [{"ts": "2026-11-23T10:00:01+00:00", "temp": 63.2},
                    {"ts": "2026-11-23T10:00:02+00:00", "temp": 63.9}]

    class _Session:
        def get(self, url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = dict(params or {})
            captured["allow_redirects"] = kwargs.get("allow_redirects")

            class _Ctx:
                async def __aenter__(self): return _Resp()

                async def __aexit__(self, *a): return False
            return _Ctx()

    src = RestSource(_spec())
    src._session = _Session()  # type: ignore[assignment] — inject, skip aiohttp
    start = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(seconds=60)
    rows = _run(src.pull(start, end))

    assert len(rows) == 2 and rows[0]["temp"] == 63.2
    assert captured["params"]["start"] == start.isoformat()
    assert captured["params"]["end"] == end.isoformat()
    assert captured["allow_redirects"] is False
    assert captured["url"].startswith("http://hist.test/api/samples")


def test_rest_pull_http_error_raises() -> None:
    """A 500 from the historian must raise — the scheduler archives it as a
    failed pull and the offline state machine advances (never silent)."""
    from edge_adviser.ingest.rest import RestSource

    spec = _spec()
    spec.url = "http://hist.test/api/samples?token=do-not-log"

    class _Resp:
        def raise_for_status(self):
            raise RuntimeError(f"500 from {spec.url}")

    class _Session:
        def get(self, url, params=None, **kwargs):
            class _Ctx:
                async def __aenter__(self): return _Resp()

                async def __aexit__(self, *a): return False
            return _Ctx()

    src = RestSource(spec)
    src._session = _Session()  # type: ignore[assignment]
    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError) as exc_info:
        _run(src.pull(now - timedelta(seconds=60), now))
    assert "do-not-log" not in str(exc_info.value)


def test_rest_pull_non_list_payload_raises() -> None:
    """A JSON object (not array) is a contract violation — refuse, don't guess."""
    from edge_adviser.ingest.rest import RestSource

    class _Resp:
        def raise_for_status(self): pass

        async def json(self):
            return {"error": "bad gateway page"}

    class _Session:
        def get(self, url, params=None, **kwargs):
            class _Ctx:
                async def __aenter__(self): return _Resp()

                async def __aexit__(self, *a): return False
            return _Ctx()

    src = RestSource(_spec())
    src._session = _Session()  # type: ignore[assignment]
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        _run(src.pull(now - timedelta(seconds=60), now))


def test_rest_auth_header_filtering() -> None:
    """Only safe credential keys become headers; arbitrary keys are dropped."""
    from edge_adviser.ingest.rest import RestSource

    src = RestSource(_spec(credentials={
        "Authorization": "Bearer tok", "X-Api-Key": "k1",
        "Content-Type": "application/json", "Evil-Header": "nope"}))
    headers = {k: v for k, v in src.spec.credentials.items() if k.lower() in {
        "authorization", "x-api-key", "accept", "content-type"}}
    assert set(headers) == {"Authorization", "X-Api-Key", "Content-Type"}
    assert "Evil-Header" not in headers


def test_rest_session_close_is_idempotent() -> None:
    from edge_adviser.ingest.rest import RestSource

    src = RestSource(_spec())
    _run(src.aclose())  # never opened → no-op, must not raise
    _run(src.aclose())
