"""Session-key conventions and provider helpers."""

from __future__ import annotations


def asset_session(asset_id: str, mode: str) -> str:
    """Stable session key isolating per-asset, per-mode history."""
    return f"asset:{asset_id}:{mode}"


MODES = {
    "monitor": "monitor",
    "report": "report",
    "qa": "qa",
    "learn": "learn",
}