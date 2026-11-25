"""REST/HTTP pull data source.

Polls an HTTP endpoint that returns a JSON array of samples in the requested
window:

    GET {url}?start={iso}&end={iso}
    -> [ {"ts": "...", "temp": 63.2, "vibration": 2.1}, ... ]

Credentials are resolved by the backend and passed via allowlisted request headers.
A timeout bounds each request so the scheduler can never block on a dead server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Mapping

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
        start_param = str(self.spec.params.get("start_param", "start"))
        end_param = str(self.spec.params.get("end_param", "end"))
        params = {
            start_param: window_start.isoformat(),
            end_param: window_end.isoformat(),
        }
        try:
            async with session.get(self.spec.url, params=params, allow_redirects=False) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"REST request failed ({type(exc).__name__})"
            ) from exc
        response_path = str(self.spec.params.get("response_path", "")).strip(".")
        if response_path:
            for key in response_path.split("."):
                if not isinstance(data, Mapping) or key not in data:
                    raise ValueError(f"REST response path not found: {response_path}")
                data = data[key]
        if not isinstance(data, list):
            # a JSON object (error page, envelope) is a contract violation —
            # refusing beats iterating a dict's keys as "samples"
            raise ValueError(
                f"rest source expected a JSON array of samples, got "
                f"{type(data).__name__} from {self.spec.url}")
        timestamp_field = str(self.spec.params.get("timestamp_field", "ts"))
        field_map = self.spec.params.get("field_map", {})
        if not isinstance(field_map, dict):
            raise ValueError("REST field_map must be a JSON object")
        normalized: list[Sample] = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("REST sample must be a JSON object")
            sample: Sample = {"ts": item.get(timestamp_field)}
            if field_map:
                for metric_key, source_key in field_map.items():
                    if not isinstance(metric_key, str) or not isinstance(source_key, str):
                        raise ValueError("REST field_map values must be strings")
                    sample[metric_key] = item.get(source_key)
            else:
                sample.update({k: v for k, v in item.items() if k != timestamp_field})
            normalized.append(sample)
        return normalized

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None