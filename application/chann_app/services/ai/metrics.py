"""AI call monitoring — Master Spec 4.1/4.5 (latency, error rate, cost).

Deliberately in-process and bounded rather than backed by Redis or a metrics
service. ADR-014's fallback criteria are stated as "p95 latency > 1.5s" and
"error rate > 5% over 100 messages" — both are read by a human deciding whether
to flip OPENROUTER_MODEL, not by an automated switcher. A rolling in-memory
window answers exactly that question, and it cannot itself become a source of
latency or an extra failure mode in the request path.

Consequence worth knowing: Cloud Run runs several instances, so each instance
sees only its own slice. Treat a single /internal/ai/metrics reading as a
sample, not a fleet-wide truth.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

# ADR-014 states the error-rate criterion over 100 messages; keep a little more
# than that so a p95 over the same window is not computed from a handful of
# samples right after a restart.
WINDOW = 200


@dataclass(frozen=True)
class Call:
    model: str
    latency_s: float
    ok: bool
    provider: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Metrics:
    def __init__(self, window: int = WINDOW):
        self._calls: deque[Call] = deque(maxlen=window)
        self._lock = threading.Lock()

    def record(self, call: Call) -> None:
        with self._lock:
            self._calls.append(call)

    def snapshot(self, model: str | None = None) -> dict:
        with self._lock:
            calls = [c for c in self._calls if model is None or c.model == model]

        if not calls:
            return {
                "samples": 0,
                "p50_latency_s": None,
                "p95_latency_s": None,
                "error_rate": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }

        # p95 over successful calls only: a timeout contributes its own timeout
        # value, which would drag the latency figure toward the ceiling and
        # double-count a failure already reflected in error_rate.
        ok_latencies = sorted(c.latency_s for c in calls if c.ok)
        errors = sum(1 for c in calls if not c.ok)

        return {
            "samples": len(calls),
            "p50_latency_s": _percentile(ok_latencies, 0.50),
            "p95_latency_s": _percentile(ok_latencies, 0.95),
            "error_rate": round(errors / len(calls), 4),
            "prompt_tokens": sum(c.prompt_tokens for c in calls),
            "completion_tokens": sum(c.completion_tokens for c in calls),
        }

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return round(sorted_values[0], 4)
    # Nearest-rank; index clamped so q=1.0 cannot run off the end.
    idx = min(int(round(q * (len(sorted_values) - 1))), len(sorted_values) - 1)
    return round(sorted_values[idx], 4)


metrics = Metrics()


class Timer:
    """Context manager that records one Call regardless of how the block exits."""

    def __init__(self, model: str):
        self.model = model
        self.provider: str | None = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.ok = False
        self._start = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        metrics.record(
            Call(
                model=self.model,
                latency_s=time.monotonic() - self._start,
                ok=self.ok and exc_type is None,
                provider=self.provider,
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
            )
        )
        return False
