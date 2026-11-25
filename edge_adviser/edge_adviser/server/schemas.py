"""Shared response schemas + helpers for the HMI API.

The backend returns a uniform envelope ``{ok: bool, data: ...|None, error: str|None}``
so the frontend can reason about success/failure without inspecting HTTP codes for
every endpoint. Writes (run/report/learn/ask) that are refused by the freshness
gate still return HTTP 200 with ``ok=false`` and a human-readable ``error`` — that
is a *deterministic refusal*, not a server fault, and the UI renders it as
"无新鲜快照，已拒绝回答/报告" rather than an exception.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
        "enabled": a.enabled,
        "scenario_notes": a.scenario_notes,
        "metrics": [metric_to_dict(m) for m in a.metrics],
    }


def source_to_dict(source: Any, *, redact_secrets: bool = False,
                   include_credential_id: bool = True) -> dict[str, Any]:
    """SourceSpec → JSON-safe form, optionally redacting URL and credential details."""
    url = source.url
    params = dict(source.params)
    if redact_secrets:
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            if parts.port:
                host = f"{host}:{parts.port}"
            query = [
                (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if not is_secret_name(key)
            ]
            url = urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))
        except ValueError:
            url = ""
        allowed_params = (
            {"timeout_s", "start_param", "end_param", "timestamp_field",
             "response_path", "field_map"}
            if source.kind.value == "rest" else {"regime", "sample_freq_s"}
        )
        params = {key: value for key, value in params.items() if key in allowed_params}
    result = {
        "kind": source.kind.value,
        "asset_id": source.asset_id,
        "params": params,
        "url": url,
        "topic": source.topic,
        "poll_interval_s": source.poll_interval_s,
    }
    if include_credential_id:
        result["credential_id"] = source.credential_id
    return result


def is_secret_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return normalized.endswith("key") or normalized in {"sig", "signature", "cookie"} or any(
        marker in normalized
        for marker in ("token", "apikey", "password", "secret", "auth", "credential")
    )