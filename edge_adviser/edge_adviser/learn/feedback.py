"""Operator feedback ingestion — writes corrections so the learner can use them."""

from __future__ import annotations

from ..core.asset_store import AssetStore


class FeedbackService:
    def __init__(self, store: AssetStore):
        self.store = store

    def record(self, asset_id: str, note: str, **extra) -> None:
        """Record an operator's correction / observation for the next learn pass."""
        self.store.append_feedback(asset_id, note, **extra)