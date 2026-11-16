"""Data source abstraction.

A DataSource yields a batch of samples for one asset. Each sample is a dict of
metric key -> numeric value attached to a timestamp. The simulator is the MVP
source; MQTT/OPC-UA/Modbus/REST are structured skeletons that lift the same
interface so the rest of the pipeline is transport-agnostic.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ..config.model import SourceSpec

Sample = dict[str, Any]  # {"ts": iso string, "<metric>": value, ...}


class DataSource(abc.ABC):
    kind: str = "base"

    def __init__(self, spec: SourceSpec):
        self.spec = spec
        self.asset_id = spec.asset_id

    @abc.abstractmethod
    async def pull(self, window_start: datetime, window_end: datetime) -> list[Sample]:
        """Return all samples in the semi-open window [window_start, window_end)."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        pass


class AsyncDataSource(abc.ABC):
    """Alternative interface for streaming sources (MQTT) — reserved."""

    kind: str = "async"

    @abc.abstractmethod
    def stream(self) -> AsyncIterator[Sample]:  # pragma: no cover
        raise NotImplementedError