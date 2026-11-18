"""Simulator data source — MVP transport.

Generates deterministic-ish time series with configurable behavior so the whole
pipeline runs offline without any external system. Supports drifting baselines,
and fault-injection regimes (shift / spike / sawtooth-on-vibration) to exercise
the detector and the LLM interpretation path.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from ..config.model import SourceKind, SourceSpec
from .base import DataSource, Sample


class SimulatorSource(DataSource):
    kind = SourceKind.SIMULATOR.value

    def __init__(self, spec: SourceSpec):
        super().__init__(spec)
        p = spec.params
        self.sample_freq_s = float(p.get("sample_freq_s", 1.0))
        self.base_temp = float(p.get("base_temp", 65.0))
        self.base_vibration = float(p.get("base_vibration", 2.0))
        self.base_rpm = float(p.get("base_rpm", 1450.0))
        # clean | shift | spike | degrade | offline | fault
        self.regime = p.get("regime", "clean")
        # hours of "already commissioned runtime" to advance the regime clock; lets
        # a demo window immediately exercise degrade/shift behavior.
        self.uptime_h = float(p.get("uptime_h", 0.0))
        self._t0 = datetime.now(timezone.utc) - timedelta(hours=8) - timedelta(hours=self.uptime_h)
        self._state: dict[str, float] = {}
        # per-source RNG seed so parallel demo assets don't share one noise stream
        self._rng = random.Random(int(p.get("seed", 42)))

    def _ws(self, window_start: datetime, window_end: datetime) -> int:
        return int((window_end.timestamp() - window_start.timestamp()) / self.sample_freq_s)

    async def pull(self, window_start: datetime, window_end: datetime) -> list[Sample]:
        # transport-level regimes short-circuit before any window math: "offline"
        # models a connected-but-silent device; "fault" a transport failure.
        if self.regime == "offline":
            return []
        if self.regime == "fault":
            raise RuntimeError("simulated transport fault")
        n = self._ws(window_start, window_end)
        if n <= 0:
            return []
        samples: list[Sample] = []
        # Anchor EVERY pull to the requested window. Anchoring only on the first
        # call and then advancing blindly made the internal clock drift against
        # real time (future-dated samples when interval < window, stale snapshots
        # when interval > window). Regime factors are pure functions of elapsed_h,
        # so re-anchoring is safe and each snapshot covers exactly its own window.
        t = window_start.timestamp()
        dt = self.sample_freq_s
        for _ in range(n):
            ts = datetime.fromtimestamp(t, tz=timezone.utc)
            elapsed_s = t - self._t0.timestamp()
            elapsed_h = elapsed_s / 3600.0
            sample = self._gen_sample(elapsed_h, t)
            sample["ts"] = ts.isoformat(timespec="milliseconds")
            samples.append(sample)
            t += dt
        return samples

    # ------------------------------------------------------------------ #
    def _gen_sample(self, elapsed_h: float, t: float) -> Sample:
        f = self._regime_factor(elapsed_h)
        noise = self._rng.gauss(0, 0.4)
        # ambient-ish slow seasonal component
        seasonal = 3.0 * math.sin(elapsed_h / 24.0 * 2 * math.pi)
        temp = self.base_temp + seasonal + noise * f

        # vibration degrades when approaching a fault window
        vib = self.base_vibration + self._vibration_bump(elapsed_h) + self._rng.gauss(0, 0.1)
        rpm = self.base_rpm + self._rng.gauss(0, 4)
        if elapsed_h > 200:
            rpm = max(900.0, rpm - 20 * math.fabs(math.sin(elapsed_h)))

        return {
            "temp": round(temp, 3),
            "vibration": round(vib, 3),
            "rpm": round(rpm, 1),
        }

    def _regime_factor(self, h: float) -> float:
        r = self.regime
        if r == "shift":
            # a slow upward drift after 120 h
            return 1.0 + (0.5 * max(0.0, h - 120.0) / 60.0)
        if r == "spike":
            # periodic short spikes
            return 1.0 + (6.0 if (int(h / 6.0) % 5 == 0) else 0.0)
        if r == "degrade":
            return 1.0 + (0.8 * math.fabs(math.sin(h / 30.0)))
        return 1.0

    def _vibration_bump(self, h: float) -> float:
        if self.regime == "degrade" and h > 100:
            return 1.5 * math.sin(h / 4.0) + max(0.0, (h - 100) / 200.0)
        return 0.0

    async def aclose(self) -> None:
        pass