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
    """Build a data source from a SourceSpec."""
    try:
        cls = _source_classes[spec.kind.value]
    except KeyError:
        raise ValueError(f"unknown source kind: {spec.kind}") from None
    return cls(spec)


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