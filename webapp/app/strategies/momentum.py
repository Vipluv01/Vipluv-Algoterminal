"""Momentum Bot: MACD histogram crossover with an EMA(50) trend filter --
mirrors opensoft-2026/trading-bots' publicly-described strategy
(feature-level description only; independent implementation, no code read
or copied).

The EMA(50) filter is the whole point of calling this "momentum" rather
than just "MACD crossover": a raw MACD histogram sign-flip fires in both
directions inside a sideways market with no real trend behind it. Requiring
price to already be above/below the EMA(50) before acting on a histogram
flip is what keeps this strategy trading WITH an established trend instead
of front-running noise.
"""

from __future__ import annotations

from app.strategies.base import MarketSnapshot, Signal
from app.strategies.indicators import ema, macd

TREND_SPAN = 50
DEFAULT_QTY = 10


class MomentumMACDStrategy:
    key = "momentum_macd"
    name = "Momentum (MACD histogram + EMA50 filter)"

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        prices = market.prices
        # MACD's own slow EMA(26) needs data; the EMA(50) trend filter needs more still.
        if len(prices) < TREND_SPAN + 2:
            return None

        _, _, hist = macd(prices)
        trend = ema(prices, TREND_SPAN)
        if any(v != v for v in (hist[-1], hist[-2], trend[-1])):
            return None

        crossed_up = hist[-2] <= 0 and hist[-1] > 0
        crossed_down = hist[-2] >= 0 and hist[-1] < 0
        above_trend = prices[-1] > trend[-1]
        below_trend = prices[-1] < trend[-1]

        if crossed_up and above_trend:
            return Signal("buy", DEFAULT_QTY, "market", None,
                          f"MACD histogram turned positive while price is above EMA{TREND_SPAN} -- momentum with the trend")
        if crossed_down and below_trend:
            return Signal("sell", DEFAULT_QTY, "market", None,
                          f"MACD histogram turned negative while price is below EMA{TREND_SPAN} -- momentum with the trend")
        return None
