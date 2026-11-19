"""Trustworthiness badge / front-matter (P3).

The deterministic analytics layer holds ground-truth facts about a snapshot's data
scope (sample count, coverage, window, provider, degradation). Rendered as the
front-matter of a report, the LLM is instructed to *reference* these values and
never re-derive or invent them — moving "prove I have data" out of the model's
responsibility and into the deterministic layer where it is auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..analytics.gates import trust_from_state
from ..config.model import AssetState, MetaData


def build_badge(
    snapshot: dict[str, Any],
    *,
    provider: str = "",
    degraded: bool = False,
    generated_at: str = "",
) -> MetaData:
    """Build a MetaData badge from a snapshot dict, tolerating missing keys."""
    state = snapshot.get("status")
    try:
        st = AssetState(state) if state else AssetState.HEALTHY
    except ValueError:
        st = AssetState.HEALTHY
    return MetaData(
        asset_id=str(snapshot.get("asset_id", "")),
        status=st,
        trust=trust_from_state(st),
        sample_count=int(snapshot.get("sample_count", 0) or 0),
        expected_samples=int(snapshot.get("expected_samples", 0) or 0),
        coverage=float(snapshot.get("coverage", 0.0) or 0.0),
        window_start=str(snapshot.get("window_start", "")),
        window_end=str(snapshot.get("window_end", "")),
        actual_span_s=float(snapshot.get("actual_span_s", 0.0) or 0.0),
        data_source=str(snapshot.get("source", "")),
        provider=provider,
        degraded=degraded,
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        state_reason=str(snapshot.get("state_reason", "")),
    )


def render_frontmatter(meta: MetaData) -> str:
    """Render the badge as an idempotent YAML-ish front-matter block."""
    return (
        "<!-- DATA-SCOPE (machine generated; do not edit) -->\n"
        "asset_id: {asset}\n"
        "status: {status}\n"
        "trust: {trust}\n"
        "sample_count: {n}\n"
        "expected_samples: {expected}\n"
        "coverage: {coverage}\n"
        "window_start: {ws}\n"
        "window_end: {we}\n"
        "actual_span_s: {span}\n"
        "data_source: {source}\n"
        "provider: {provider}\n"
        "degraded: {degraded}\n"
        "generated_at: {gen}\n"
        "state_reason: {reason}\n"
    ).format(
        asset=meta.asset_id,
        status=meta.status.value,
        trust=meta.trust.value,
        n=meta.sample_count,
        expected=meta.expected_samples,
        coverage=meta.coverage,
        ws=meta.window_start,
        we=meta.window_end,
        span=meta.actual_span_s,
        source=meta.data_source,
        provider=meta.provider,
        degraded="true" if meta.degraded else "false",
        gen=meta.generated_at,
        reason=meta.state_reason or "-",
    )