"""Configuration loading and file I/O for EdgeAdviser.

Adviser config is stored as JSON documents: an EdgeAdviser file plus per-asset
profiles (ASSET.md is rendered from the asset spec). This keeps configs
human-editable and diff-friendly while still being validated into dataclasses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import (
    AdviserConfig,
    AssetSpec,
    MetricSpec,
    ProviderSpec,
    SourceSpec,
    SourceKind,
    ModelRole,
)


def _metric_from_dict(d: dict[str, Any]) -> MetricSpec:
    return MetricSpec(
        key=d["key"],
        unit=d.get("unit", ""),
        norm_min=d.get("norm_min"),
        norm_max=d.get("norm_max"),
        warn_min=d.get("warn_min"),
        warn_max=d.get("warn_max"),
        crit_min=d.get("crit_min"),
        crit_max=d.get("crit_max"),
        description=d.get("description", ""),
        persist=d.get("persist", 1),
        min_samples=d.get("min_samples", 0),
    )


def asset_from_dict(d: dict[str, Any]) -> AssetSpec:
    return AssetSpec(
        asset_id=d["asset_id"],
        name=d.get("name", d["asset_id"]),
        description=d.get("description", ""),
        kind=d.get("kind", "machine"),
        metrics=[_metric_from_dict(m) for m in d.get("metrics", [])],
        scenario_notes=list(d.get("scenario_notes", [])),
        sample_freq_s=d.get("sample_freq_s", 1.0),
        window_s=d.get("window_s", 60),
        downtime_windows=list(d.get("downtime_windows", [])),
    )


def load_config(path: str | Path) -> AdviserConfig:
    """Load an EdgeAdviser config JSON into dataclasses."""
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)
    raw = dict(raw)
    raw.setdefault("defaults", {})
    raw["defaults"] = dict(raw["defaults"])
    raw["defaults"].setdefault("_source_file", str(p.resolve()))

    providers: dict[str, ProviderSpec] = {}
    for role, spec in (raw.get("providers") or {}).items():
        providers[role] = ProviderSpec(
            role=ModelRole(role),
            model_preset=spec.get("model_preset", ""),
            model=spec.get("model", ""),
            api_base=spec.get("api_base", ""),
            timeout_s=spec.get("timeout_s", 120.0),
            max_tokens=spec.get("max_tokens", 4096),
        )

    assets = [asset_from_dict(d) for d in raw.get("assets", [])]

    sources: list[SourceSpec] = []
    for d in raw.get("sources", []):
        sources.append(SourceSpec(
            kind=SourceKind(d["kind"]),
            asset_id=d.get("asset_id", ""),
            params=d.get("params", {}),
            url=d.get("url", ""),
            topic=d.get("topic", ""),
            poll_interval_s=d.get("poll_interval_s", 1.0),
            credentials=d.get("credentials", {}),
        ))

    return AdviserConfig(
        workspace=Path(raw.get("workspace", ".")),
        defaults=raw.get("defaults", {}),
        providers=providers,
        assets=assets,
        sources=sources,
        monitor_interval_s=raw.get("monitor_interval_s", 60.0),
        deep_report_cron=raw.get("deep_report_cron", "0 6 * * *"),
        learn_cron=raw.get("learn_cron", "0 3 * * *"),
        history_top_k=raw.get("history_top_k", 6),
        history_max_chars=raw.get("history_max_chars", 2400),
        data_freshness_s=raw.get("data_freshness_s", 3600),
        coverage_threshold=raw.get("coverage_threshold", 0.8),
        max_empty_windows=raw.get("max_empty_windows", 3),
        analysis_regime=raw.get("analysis_regime", "clean"),
        learn_gate=raw.get("learn_gate", "soft"),
        memory_index=raw.get("memory_index", "off"),
        memory_conflict_policy=raw.get("memory_conflict_policy", "flag"),
        memory_decay_s=raw.get("memory_decay_s", 7776000),
        runlog_enabled=raw.get("runlog_enabled", True),
    )


def asset_to_asset_md(asset: AssetSpec) -> str:
    """Render an asset profile into ASSET.md (scene metadata / background knowledge)."""
    lines: list[str] = [
        f"# Asset: {asset.name}",
        f"id: {asset.asset_id}",
        f"kind: {asset.kind}",
        f"description: {asset.description}",
        "",
        "## Parameters",
    ]
    for m in asset.metrics:
        band = ""
        if m.norm_min is not None or m.norm_max is not None:
            band += f"normal=[{m.norm_min or '-inf'},{m.norm_max or 'inf'}]"
        if m.warn_max is not None or m.warn_min is not None:
            band += f" warn=[{m.warn_min or '-inf'},{m.warn_max or 'inf'}]"
        if m.crit_max is not None or m.crit_min is not None:
            band += f" critical=[{m.crit_min or '-inf'},{m.crit_max or 'inf'}]"
        lines.append(f"- `{m.key} [{m.unit}]` {m.description} {band}".rstrip())

    if asset.scenario_notes:
        lines += ["", "## Scenario Background", *[f"- {n}" for n in asset.scenario_notes]]
    return "\n".join(lines)


def ensure_default_asset_md(asset: AssetSpec, assets_dir: Path) -> Path:
    """Write ASSET.md on first run; returns its path."""
    d = assets_dir / asset.asset_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "ASSET.md"
    if not p.exists():
        p.write_text(asset_to_asset_md(asset), encoding="utf-8")
    return p