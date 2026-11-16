"""Domain models and configuration for EdgeAdviser.

This is the contract everything else in the system builds against: the asset
registry (what equipment exists, its parameter specs, normal ranges and alarm
thresholds), the data sources that feed it, and the provider/LLM settings for
online vs offline model routing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Enums / value objects
# --------------------------------------------------------------------------- #
class SourceKind(str, Enum):
    SIMULATOR = "simulator"
    MQTT = "mqtt"
    OPCUA = "opcua"
    MODBUS = "modbus"
    REST = "rest"


class TrendKind(str, Enum):
    SAWTOOTH = "sawtooth"
    WANDER = "wander"
    SHIFT = "shift"
    SPIKE = "spike"
    CLEAN = "clean"


class AlertLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


class ModelRole(str, Enum):
    ONLINE = "online"   # strong model, e.g. hosted API
    OFFLINE = "offline"  # local Ollama
    ROUTINE = "routine"  # cheap local path for routine monitoring


# --------------------------------------------------------------------------- #
# Metric spec: one measurable parameter of an asset.
# --------------------------------------------------------------------------- #
@dataclass
class MetricSpec:
    key: str
    unit: str = ""
    norm_min: float | None = None
    norm_max: float | None = None
    # alarm bands: a warning fires above warn_max / below warn_min, etc.
    warn_min: float | None = None
    warn_max: float | None = None
    crit_min: float | None = None
    crit_max: float | None = None
    description: str = ""
    # how long a value must stay outside a band (in samples/window) before it counts
    persist: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict-like(self)


# --------------------------------------------------------------------------- #
# Asset spec: one piece of equipment.
# --------------------------------------------------------------------------- #
@dataclass
class AssetSpec:
    asset_id: str
    name: str
    description: str = ""
    kind: str = "machine"
    metrics: list[MetricSpec] = field(default_factory=list)
    # free-text scene metadata injected as context (the "background knowledge")
    scenario_notes: list[str] = field(default_factory=list)
    # sampling
    sample_freq_s: float = 1.0
    window_s: int = 60

    def metric(self, key: str) -> MetricSpec | None:
        return next((m for m in self.metrics if m.key == key), None)


# --------------------------------------------------------------------------- #
# Data source spec
# --------------------------------------------------------------------------- #
@dataclass
class SourceSpec:
    kind: SourceKind
    asset_id: str
    # simulator params
    params: dict[str, Any] = field(default_factory=dict)
    # transport
    url: str = ""
    topic: str = ""
    poll_interval_s: float = 1.0
    credentials: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Provider spec (online vs offline model routing)
# --------------------------------------------------------------------------- #
@dataclass
class ProviderSpec:
    role: ModelRole
    model_preset: str = ""
    model: str = ""
    api_base: str = ""
    timeout_s: float = 120.0
    max_tokens: int = 4096


# --------------------------------------------------------------------------- #
# Top-level advisor config
# --------------------------------------------------------------------------- #
@dataclass
class AdviserConfig:
    workspace: Path = Path(".")

    defaults: dict[str, Any] = field(default_factory=dict)
    providers: dict[str, ProviderSpec] = field(default_factory=dict)
    assets: list[AssetSpec] = field(default_factory=list)
    sources: list[SourceSpec] = field(default_factory=list)

    monitor_interval_s: float = 60.0
    deep_report_cron: str = "0 6 * * *"  # daily 6am deep report
    learn_cron: str = "0 3 * * *"        # daily 3am history learning

    # context budget for offline model routing
    history_top_k: int = 6
    history_max_chars: int = 2400

    def asset(self, asset_id: str) -> AssetSpec | None:
        return next((a for a in self.assets if a.asset_id == asset_id), None)


def asdict_like(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance (or object) to a plain dict for JSON."""
    if isinstance(obj, dataclass):
        return asdict(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)