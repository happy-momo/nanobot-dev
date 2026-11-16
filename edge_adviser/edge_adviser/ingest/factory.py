"""Placeholder skeletons for industrial field protocols.

These are intentionally thin: the base interface (pull(sample window)) is what
matters to the rest of the pipeline. Each real adapter needs the corresponding
third-party client (paho-mqtt / asyncua / pymodbus) which we do not hard-depend
on in the MVP. Wire them up once a field deployment names the endpoint.
"""

from __future__ import annotations

import abc
from datetime import datetime

from ..config.model import SourceKind, SourceSpec
from .base import DataSource, Sample


class MqttSource(DataSource, abc.ABC):
    kind = SourceKind.MQTT.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        raise NotImplementedError(
            "MQTT source not yet wired. paho-mqtt client + topic subscription "
            "belted into `pull` by querying a short retention buffer."
        )


class OpcUaSource(DataSource, abc.ABC):
    kind = SourceKind.OPCUA.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        raise NotImplementedError(
            "OPC-UA source not yet wired. asyncua client browsing the node set "
            "and reading the configured tags over [window_start, window_end)."
        )


class ModbusSource(DataSource, abc.ABC):
    kind = SourceKind.MODBUS.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        raise NotImplementedError(
            "Modbus source not yet wired. pymodbus polling registers at "
            "spec.credentials/params.asset_id addressing."
        )


# Keep the interface honest for static analysis.
def _protocol_anchor(sample: Sample, start: datetime, end: datetime) -> None:  # pragma: no cover
    pass