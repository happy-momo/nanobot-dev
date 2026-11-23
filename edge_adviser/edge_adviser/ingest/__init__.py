from .base import DataSource, AsyncDataSource, Sample
from .factory import MqttSource, ModbusSource, OpcUaSource
from .rest import RestSource
from .simulator import SimulatorSource

_source_classes = {
    SimulatorSource.kind: SimulatorSource,
    RestSource.kind: RestSource,
    MqttSource.kind: MqttSource,      # raises until wired
    OpcUaSource.kind: OpcUaSource,    # raises until wired
    ModbusSource.kind: ModbusSource,  # raises until wired
}


def create_source(spec) -> DataSource:
    """Build a data source from a SourceSpec.

    Unknown kinds raise ValueError; unwired protocol skeletons raise
    NotImplementedError (directly or via the ABC TypeError when ``pull`` is
    unimplemented) — both are "configured but not available" and must be loud
    at startup, never a silent no-op source.
    """
    try:
        cls = _source_classes[spec.kind.value]
    except (KeyError, AttributeError):
        raise ValueError(f"unknown source kind: {spec.kind}") from None
    try:
        return cls(spec)
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