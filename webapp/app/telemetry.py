"""Real, measured latency -- not the Go benchmark's numbers borrowed up a
layer. bourse's own bench/ results (results/latency.json) measure the
matching engine's IN-PROCESS book operations in nanoseconds; this module
measures something entirely different: the actual Python<->simserver IPC
round-trip for a real order submit, in milliseconds, as this specific
webapp process experiences it (JSON serialize, write to the subprocess's
stdin, block on readline, JSON deserialize -- see sim/bourse_sim/
engine.py's Engine._call). Reusing the Go figure here would recreate
exactly the class of bug this project's own README benchmark-drift fix
(bourse/README.md, verified against results/latency.json) already caught
once: a number presented as measuring one thing that was actually
measuring another.

StatusBar.js's own comment states the rule this module exists to honor:
never fake a metric. Render "-" before the first real sample -- that's
the frontend's job. This module's job is to never hand it a fabricated or
zero value to render instead.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

# Bounded, not unbounded -- this is a RECENT-performance indicator (a
# StatusBar readout), not an audit log; nothing downstream needs latency
# samples from an hour ago once thousands of newer ones exist. 2000
# samples is a few minutes of ordinary order-flow activity (bots included
# -- see app/markets.py's SymbolMarket._wrap_engine_submit_to_record_fills,
# which times EVERY submit() through the real IPC pipe, not just
# human-submitted orders, since it's the same subprocess and the same
# channel either way).
_MAX_SAMPLES = 2000
_order_submit_latencies_ms: deque[float] = deque(maxlen=_MAX_SAMPLES)


def record_order_submit_latency_ms(latency_ms: float) -> None:
    _order_submit_latencies_ms.append(latency_ms)


def reset_order_submit_latencies() -> None:
    """Called once per app lifespan startup (app/main.py) -- this is a
    module-level global, and a fresh process (or a fresh registry/engine
    subprocess in tests) has no continuity with whatever a PREVIOUS
    process's latency samples measured. Same reasoning, and the same real
    leak this fixes, as app.pairs_service.reset_pair_telemetry."""
    _order_submit_latencies_ms.clear()


@dataclass(frozen=True)
class LatencyPercentiles:
    n_samples: int
    p50_ms: float
    p99_ms: float


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list -- no numpy
    dependency needed for this, and the sample counts here (up to 2000)
    make the exact interpolation method immaterial to two decimal places."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = min(len(sorted_values) - 1, max(0, round(pct / 100.0 * (len(sorted_values) - 1))))
    return sorted_values[idx]


def order_submit_latency_percentiles() -> LatencyPercentiles | None:
    """None before the first real sample -- never a fabricated 0.0 or a
    made-up placeholder. A caller (the telemetry router) that gets None
    back must render that as "no data yet," not as "latency is zero,"
    the same distinction this project's other None-means-no-measurement
    fields (win_rate, profit_factor, sharpe_ratio) already draw."""
    if not _order_submit_latencies_ms:
        return None
    values = sorted(_order_submit_latencies_ms)
    return LatencyPercentiles(n_samples=len(values), p50_ms=_percentile(values, 50), p99_ms=_percentile(values, 99))
