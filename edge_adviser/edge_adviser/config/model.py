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
    # Trustworthiness-first additions: these do NOT mean a real alarm fired.
    # Real production data still yields only info/warn/critical. INSUFFICIENT
    # (n>0 but below min_samples) and UNKNOWN (n==0) are data-coverage verdicts
    # so that sparse/empty windows are never reported as "nominal".
    INSUFFICIENT = "insufficient"
    UNKNOWN = "unknown"


class AssetState(str, Enum):
    """Machine/data-state verdict used across snapshot + read-gates + learn gate.

    ``healthy``/``partial`` are the only states where the data is trustworthy
    enough for confident conclusions. The rest are data-integrity verdicts that
    must stop (or downgrade) model interpretation.
    """
    HEALTHY = "healthy"            # data sufficient + fresh
    PARTIAL = "partial"            # data present but coverage below threshold
    INSUFFICIENT = "insufficient"  # data present but below min_samples
    UNKNOWN = "unknown"            # no data at all (n==0)
    OFFLINE = "offline"            # consecutive empty windows -> abnormal disconnection
    STALE = "stale"                # data exists but read past its freshness window
    PLANNED_DOWNTIME = "planned_downtime"  # inside a configured planned-downtime window


class TrustLevel(str, Enum):
    """Deterministic trust mapping from AssetState (pure logic, not LLM-judged)."""
    FULL = "full"
    PARTIAL = "partial"
    LOW = "low"
    NONE = "none"


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
    # minimum number of samples required before this metric yields a confident
    # (info/warn/critical) verdict. 0 = no lower bound (legacy behavior). Below
    # this the metric is tagged INSUFFICIENT so sparse data is never reported
    # as nominal — a data-quality gate, not an alarm.
    min_samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Trustworthiness badge / metadata + audit ledger record.
# --------------------------------------------------------------------------- #
@dataclass
class MetaData:
    """Machine-generated ground-truth about a snapshot's data scope.

    Rendered as the front-matter of a report so the LLM references *deterministic*
    facts about coverage/provider/degradation instead of re-deriving (and possibly
    inventing) them. Constructor-level fields are set by the allocator; the rest
    default to a safe/empty state so a minimal dict never crashes rendering.
    """
    asset_id: str = ""
    status: AssetState = AssetState.HEALTHY
    trust: TrustLevel = TrustLevel.FULL
    sample_count: int = 0
    expected_samples: int = 0
    coverage: float = 0.0              # 0..1 ratio of actual / expected samples
    window_start: str = ""
    window_end: str = ""
    actual_span_s: float = 0.0
    data_source: str = ""
    provider: str = ""
    degraded: bool = False
    generated_at: str = ""
    state_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["trust"] = self.trust.value
        return d


@dataclass
class RunLogEntry:
    """One append-only audit record saying what happened (incl. failures) so a
    missing report day is traceable instead of silently absent."""
    asset_id: str = ""
    date: str = ""            # YYYY-MM-DD
    kind: str = ""            # run_once|monitor|deep_report|learn|qa|serve_start
    outcome: str = ""         # ok|no_data|provider_failed|exception|gated
    reason: str = ""
    detail: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    # planned-downtime windows during which an empty window is EXPECTED (not an
    # offline fault). Each entry: {"start": iso, "end": iso}. Empty = no windows.
    downtime_windows: list[dict] = field(default_factory=list)

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

    # ---- trustworthiness / data-integrity tuning (defaults == legacy behavior) ----
    # P1: how old a snapshot (window_end vs now) may be before it is treated as
    # missing/stale by the read gates (ask/monitor/report) and the learn gate.
    data_freshness_s: int = 3600
    # N2: coverage ratio (actual/expected samples) below this marks the snapshot
    # PARTIAL (analyzable but low-confidence).
    coverage_threshold: float = 0.8
    # N4: consecutive empty windows before the machine is judged OFFLINE
    # (abnormal disconnection), as opposed to a transient gap.
    max_empty_windows: int = 3
    # N9: regime label injected by the scheduler (replaces the hardcoded
    # "degrade"); a transport label, not the simulator's internal regime.
    analysis_regime: str = "clean"
    # N7: learn input gate. off = legacy unconditional; soft = inject a
    # deterministic [confidence: low] declaration and write back; strict = skip
    # the learn pass (no LLM, no MEMORY.md write) on untrustworthy input.
    learn_gate: str = "soft"
    # N6: memory retrieval. off = legacy "most recent" retrieval; keywords = a
    # lightweight stdlib relevance index (query-driven top-k).
    memory_index: str = "off"
    # N8: memory conflict/staleness handling. off = ignore; flag = track claims
    # and mark numeric contradictions + expired facts for the retriever.
    memory_conflict_policy: str = "flag"
    # N8: a claim older than this (seconds) is considered stale during retrieval.
    memory_decay_s: int = 7776000  # 90 days
    # P2: record append-only runlog.jsonl for every asset.
    runlog_enabled: bool = True

    # ---- HMI (human-machine interface) settings ----------------------------
    # Optional shared access PIN for the web UI. "none" (default) = LAN open.
    # Any other string enables a shared-pin gate: the UI exchanges it for a
    # short-lived HMAC token via POST /api/auth/pin.
    hmi_pin: str = "none"
    # Web server bind config for the built-in `edge-adviser serve --web` mode.
    hmi_host: str = "0.0.0.0"
    hmi_port: int = 8000

    # Demo mode: inject a deterministic MockGateway (no nanobot/LLM needed) and
    # seed the workspace with rich historical data on startup. For local testing
    # and offline presentations; production configs leave this false.
    demo: bool = False

    def asset(self, asset_id: str) -> AssetSpec | None:
        return next((a for a in self.assets if a.asset_id == asset_id), None)


def asdict_like(obj: Any) -> dict[str, Any]:
    """Convert a dataclass instance (or object) to a plain dict for JSON."""
    if isinstance(obj, dataclass):
        return asdict(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)