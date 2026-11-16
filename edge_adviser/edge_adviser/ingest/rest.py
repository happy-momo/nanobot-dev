"""REST/HTTP pull data source.

Polls an HTTP endpoint that returns a JSON array of samples in the requested
window:

    GET {url}?start={iso}&end={iso}
    -> [ {"ts": "...", "temp": 63.2, "vibration": 2.1}, ... ]

Credentials can be passed via spec.credentials (header-based) or as url params.
A timeout bounds each request so the scheduler can never block on a dead server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import aiohttp

from ..config.model import SourceKind, SourceSpec
from .base import DataSource, Sample


class RestSource(DataSource):
    kind = SourceKind.REST.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        self._timeout = aiohttp.ClientTimeout(total=float(spec.params.get("timeout_s", 20)))
        self._session: aiohttp.ClientSession | None = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None:
            headers = {k: v for k, v in self.spec.credentials.items() if k.lower() in {
                "authorization", "x-api-key", "accept", "content-type"}}
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=headers or None)
        return self._session

    async def pull(self, window_start: datetime, window_end: datetime) -> list[Sample]:
        session = await self._session_get()
        params = {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        }
        async with session.get(self.spec.url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return list(data)

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


async def _skeleton():  # pragma: no cover
    raise NotImplementedError