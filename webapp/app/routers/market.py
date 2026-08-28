"""Historical OHLC bars -- the seeding path for a chart's first paint.

Without this, CandleChart.js's own aggregator (useCandleAggregator) starts
with ZERO candles and fills in only as live WebSocket ticks arrive, so a
freshly loaded chart is blank for the first several seconds (worse: blank
for up to an HOUR on a 1hr candle at one tick per second) -- a real
first-impression problem, not a cosmetic one. This endpoint exists so the
frontend can seed a chart's history in one request, then hand off to the
live tick stream for anything after "now" -- swapping the data source to
a real broker feed in Phase 7 only changes where the bars in THIS
endpoint's response come from, not how the chart consumes them.

Bars are built from SymbolMarket.price_history (app/markets.py) -- one
real close price per simulated second, unbounded for the life of the
process (see that field's own definition) -- aggregated into
interval-sized OHLC buckets. There is no sub-second tick data to build a
"true" open/high/low from within one simulated second, so a bar's
open/high/low/close over a short interval (1m = 60 underlying points) are
real extrema of real per-second closes, the same honest construction any
OHLC series built from a coarser-than-tick feed uses.
"""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.markets import DERIVED_INDICES, MarketRegistry
from app.routers.orders import get_registry

router = APIRouter(prefix="/market", tags=["market"])

# interval key -> seconds per bar. Matches CandleChart.js's own
# CANDLE_SECONDS_OPTIONS keys (1s/5s/1m/5m) plus the two longer ones the
# product now needs (30m/1hr) -- since without backfill, a 1hr candle at
# one tick/second takes a full hour of live watching to ever draw a single
# complete bar.
INTERVAL_SECONDS: dict[str, int] = {
    "1s": 1, "5s": 5, "1m": 60, "5m": 300, "30m": 1800, "1hr": 3600,
}

# Hard ceiling on bars returned per request -- same "bounded, not
# unbounded" reasoning as GET /orders' page-size cap: nothing downstream
# needs more than one screen's worth of candles at a time, and price_history
# being unbounded server-side (see this module's own docstring) is exactly
# why the endpoint reading it must not be.
MAX_BARS = 1000
DEFAULT_BARS = 200


class BarOut(BaseModel):
    # Milliseconds, matching CandleChart.js's own timestamp convention
    # (Date.now()-bucketed) -- see this module's docstring on how these
    # are synthesized from price_history, which has no wall-clock stamps
    # of its own to read back.
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    # None (not 0) when this bar falls outside SymbolMarket.recent_volume's
    # own bounded window (maxlen=500 -- see app/markets.py) -- price
    # history is unbounded but volume history isn't, and a bar older than
    # that window genuinely has no real traded-quantity figure to report,
    # which is a different claim than "zero volume traded." Always None
    # for a derived index (NIFTY50/BANKNIFTY): there is no single
    # instrument's volume to report for a synthetic basket.
    volume: int | None


class HistoryOut(BaseModel):
    symbol: str
    interval: str
    requested_bars: int
    # How many bars this response actually contains -- may be LESS than
    # requested_bars when the underlying price_history is shorter than
    # requested_bars * interval_seconds (e.g. right after server startup).
    # Never padded with synthetic bars to make this number look complete --
    # see this module's docstring: a short real series is honest, invented
    # bars are not.
    returned_bars: int
    bars: list[BarOut]


def _aggregate_bars(
    prices: list[float], volumes: list[int | None], *, interval_seconds: int, limit: int, now_ms: int,
) -> list[BarOut]:
    """Buckets per-second (price, volume) points into interval_seconds-wide
    OHLCV bars, using the SAME absolute wall-clock bucketing formula
    CandleChart.js's own live aggregator uses (bucket = floor(ts_ms /
    interval_ms), bar timestamp = bucket * interval_ms) -- not bucketed
    relative to the end of this response. That match matters: it's what
    lets a live tick appended right after this seed data land in the
    correct bucket (extending the last historical bar, or starting a new
    one) instead of creating a duplicate, gapped, or misaligned bar right
    at the seed/live seam.

    prices[i] is assigned a synthesized timestamp counting backward from
    now_ms in exact interval_seconds=1 steps -- see this module's own
    docstring for why that's a real timestamp, not an approximation: the
    live tick loop (app/main.py's MARKET_TICK_SECONDS=1.0) genuinely does
    step the registry once per real wall-clock second, so "the most recent
    price_history point happened now, and each prior one exactly one real
    second before that" is what actually occurred whenever the server has
    been live-ticking continuously.
    """
    n = len(prices)
    interval_ms = interval_seconds * 1000
    bars: dict[int, BarOut] = {}
    order: list[int] = []
    for i in range(n):
        ts_ms = now_ms - (n - 1 - i) * 1000
        bucket = (ts_ms // interval_ms) * interval_ms
        price = prices[i]
        vol = volumes[i]
        existing = bars.get(bucket)
        if existing is None:
            bars[bucket] = BarOut(timestamp=bucket, open=price, high=price, low=price, close=price, volume=vol)
            order.append(bucket)
        else:
            existing.high = max(existing.high, price)
            existing.low = min(existing.low, price)
            existing.close = price
            if existing.volume is not None and vol is not None:
                existing.volume += vol
            elif vol is not None:
                existing.volume = vol
    ordered = [bars[b] for b in order]
    return ordered[-limit:]


@router.get("/history", response_model=HistoryOut)
def get_history(
    symbol: str,
    interval: Literal["1s", "5s", "1m", "5m", "30m", "1hr"] = "1m",
    limit: int = Query(DEFAULT_BARS, gt=0, le=MAX_BARS),
    registry: MarketRegistry = Depends(get_registry),
):
    if symbol not in registry.markets and symbol not in DERIVED_INDICES:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r}")

    interval_seconds = INTERVAL_SECONDS[interval]
    prices = registry.price_history_for(symbol).tolist()

    is_derived = symbol not in registry.markets
    if is_derived:
        volumes: list[int | None] = [None] * len(prices)
    else:
        market = registry.markets[symbol]
        recent_vol = list(market.recent_volume)
        # recent_volume is a BOUNDED trailing window (maxlen=500) kept in
        # lockstep with price_history (both appended once per step() call
        # -- see app/markets.py) -- so it covers exactly the last
        # len(recent_vol) entries of prices, never more. Everything older
        # gets None, not a fabricated 0.
        n_unknown = len(prices) - len(recent_vol)
        volumes = [None] * n_unknown + recent_vol

    # Enough raw points to plausibly fill `limit` bars at this interval,
    # capped to what actually exists -- no need to bucket the ENTIRE
    # unbounded price_history when only the most recent `limit` bars are
    # wanted.
    needed_points = min(len(prices), limit * interval_seconds + interval_seconds)
    prices = prices[-needed_points:] if needed_points else []
    volumes = volumes[-needed_points:] if needed_points else []

    now_ms = int(time.time() * 1000)
    bars = _aggregate_bars(prices, volumes, interval_seconds=interval_seconds, limit=limit, now_ms=now_ms)

    return HistoryOut(symbol=symbol, interval=interval, requested_bars=limit, returned_bars=len(bars), bars=bars)
