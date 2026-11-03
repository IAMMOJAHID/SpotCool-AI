"""StageMetrics: each stage's wall-time latency (p50, p95), and the model-call ledger, as ASCII.

The building loop times every stage of a step (``snapshot``, ``detect``, ``actuate``, ``step``)
with ``time.perf_counter``; the summary is logged every ``LOG_EVERY_STEPS`` steps and at stop.
"""

import statistics
from collections import deque
from collections.abc import Iterable

from spotcool_agent.services.meta.client import Call

SAMPLES = 500
"""Samples kept per stage; the oldest go first."""
LOG_EVERY_STEPS = 8
"""Steps between two logged summaries."""


class StageMetrics:
    """Latency samples per stage (see the module docstring)."""

    def __init__(self) -> None:
        self._samples: dict[str, deque[float]] = {}
        self.late = 0
        """Results that came back after their step's deadline and were discarded."""

    def add(self, stage: str, seconds: float) -> None:
        """Record one sample of a stage, in wall seconds."""
        self._samples.setdefault(stage, deque(maxlen=SAMPLES)).append(seconds)

    def percentiles(self, stage: str) -> tuple[float, float] | None:
        """A stage's p50 and p95 in seconds, or ``None`` without samples."""
        samples = sorted(self._samples.get(stage, ()))
        if not samples:
            return None
        p95 = samples[min(len(samples) - 1, round(0.95 * (len(samples) - 1)))]
        return statistics.median(samples), p95

    def summary(self) -> str:
        """Every stage as ``name p50 0.012 s p95 0.030 s (n=48)``, on one line."""
        parts = []
        for stage, samples in self._samples.items():
            p50, p95 = self.percentiles(stage) or (0.0, 0.0)
            parts.append(f"{stage} p50 {p50:.3f} s p95 {p95:.3f} s (n={len(samples)})")
        return "; ".join(parts) or "no samples"


def ledger_summary(calls: Iterable[Call]) -> str:
    """The model calls by model: count, failures, latency p50 and p95, on one line."""
    by_model: dict[str, list[Call]] = {}
    for call in calls:
        by_model.setdefault(call.model, []).append(call)
    parts = []
    for model, made in by_model.items():
        latencies = sorted(call.latency_s for call in made)
        p95 = latencies[min(len(latencies) - 1, round(0.95 * (len(latencies) - 1)))]
        failed = sum(not call.ok for call in made)
        parts.append(
            f"{model}: {len(made)} calls, {failed} failed, p50 {statistics.median(latencies):.2f} s"
            f" p95 {p95:.2f} s"
        )
    return "; ".join(parts) or "no model calls"
