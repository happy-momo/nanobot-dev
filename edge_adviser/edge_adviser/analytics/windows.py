"""Downsampling and cleaning helpers for raw sample batches."""

from __future__ import annotations

import numpy as np

from ..config.model import MetricSpec
from ..ingest.base import Sample


def clean_series(values: list[float]) -> np.ndarray:
    """Drop NaN/inf, returning a fin array."""
    y = np.asarray(values, dtype=np.float64)
    y = y[np.isfinite(y)]
    return y


def downsample(values: list[float], factor: int = 1) -> list[float]:
    """Box average every `factor` samples; factor<=1 returns as-is."""
    if factor <= 1 or len(values) < 2:
        return list(values)
    y = np.asarray(values, dtype=np.float64)
    n = len(y)
    k = len(y) // factor
    if k == 0:
        return [float(y.mean())]
    trimmed = y[: k * factor].reshape(k, factor).mean(axis=1)
    return [float(v) for v in trimmed]