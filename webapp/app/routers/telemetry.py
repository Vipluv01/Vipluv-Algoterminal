"""Real system-performance telemetry -- see app/telemetry.py's own
docstring on why this is a genuinely different measurement from bourse's
Go-side benchmark numbers, and must never borrow them.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.telemetry import order_submit_latency_percentiles

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


class LatencyOut(BaseModel):
    # None (with no p50_ms/p99_ms) before the first real order submit --
    # the frontend renders "-" for that case; this endpoint's job is only
    # to never send a fabricated number instead.
    n_samples: int
    p50_ms: float
    p99_ms: float


@router.get("/latency", response_model=LatencyOut | None)
def get_order_submit_latency():
    percentiles = order_submit_latency_percentiles()
    if percentiles is None:
        return None
    return LatencyOut(n_samples=percentiles.n_samples, p50_ms=percentiles.p50_ms, p99_ms=percentiles.p99_ms)
