from ..config.model import SourceKind
from .base import DataSource, AsyncDataSource, Sample
from .modbus import ModbusSource
from .mqtt import MqttSource
from .opcua import OpcUaSource
from .rest import RestSource
from .simulator import SimulatorSource
from dataclasses import replace

_source_classes = {
    SimulatorSource.kind: SimulatorSource,
    RestSource.kind: RestSource,
    MqttSource.kind: MqttSource,
    OpcUaSource.kind: OpcUaSource,
    ModbusSource.kind: ModbusSource,
}


def create_source(spec, credentials: dict[str, str] | None = None) -> DataSource:
    """Build a data source from a SourceSpec.

    Unknown kinds and invalid connector configurations fail loudly.
    """
    try:
        cls = _source_classes[spec.kind.value]
    except (KeyError, AttributeError):
        raise ValueError(f"unknown source kind: {spec.kind}") from None
    try:
        resolved = replace(spec, credentials=credentials) if credentials is not None else spec
        if resolved.kind == SourceKind.REST and set(resolved.credentials) - {
            "authorization", "x-api-key",
        }:
            raise ValueError("非 REST 请求头凭据不能用于 REST 连接器")
        return cls(resolved)
    except TypeError as exc:
        if "abstract" in str(exc):
            raise NotImplementedError(
                f"{spec.kind.value} source is a skeleton (abstract pull); "
                "wire the protocol client before use") from exc
        raise


__all__ = [
    "AsyncDataSource",
    "DataSource",
    "MqttSource",
    "ModbusSource",
    "OpcUaSource",
    "RestSource",
    "SimulatorSource",
    "Sample",
    "create_source",
]