"""Shared response schemas + helpers for the HMI API.

The backend returns a uniform envelope ``{ok: bool, data: ...|None, error: str|None}``
so the frontend can reason about success/failure without inspecting HTTP codes for
every endpoint. Writes (run/report/learn/ask) that are refused by the freshness
gate still return HTTP 200 with ``ok=false`` and a human-readable ``error`` — that
is a *deterministic refusal*, not a server fault, and the UI renders it as
"无新鲜快照，已拒绝回答/报告" rather than an exception.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def envelope(ok: bool, data: Any = None, error: str | None = None, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": ok, "data": data, "error": error}, status_code=status)


def ok(data: Any = None) -> JSONResponse:
    return envelope(True, data=data)


def fail(error: str, status: int = 200, data: Any = None) -> JSONResponse:
    return envelope(False, data=data, error=error, status=status)


def metric_to_dict(m: Any) -> dict[str, Any]:
    """MetricSpec → JSON-friendly dict (norm/warn/crit bands + persist + min_samples)."""
    return {
        "key": m.key,
        "unit": m.unit,
        "description": m.description,
        "norm_min": m.norm_min,
        "norm_max": m.norm_max,
        "warn_min": m.warn_min,
        "warn_max": m.warn_max,
        "crit_min": m.crit_min,
        "crit_max": m.crit_max,
        "persist": m.persist,
        "min_samples": m.min_samples,
    }


def asset_to_dict(a: Any) -> dict[str, Any]:
    """AssetSpec → JSON-friendly dict."""
    return {
        "asset_id": a.asset_id,
        "name": a.name,
        "kind": a.kind,
        "description": a.description,
        "sample_freq_s": a.sample_freq_s,
        "window_s": a.window_s,
        "downtime_windows": a.downtime_windows,
        "scenario_notes": a.scenario_notes,
        "metrics": [metric_to_dict(m) for m in a.metrics],
    }