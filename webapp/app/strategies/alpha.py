"""Alpha Bot: RSI + EMA crossover -- mirrors opensoft-2026/trading-bots'
publicly-described strategy (feature-level description only; this is an
independent implementation, no code from that repo was read or copied).

Enters long on a bullish EMA(9)/EMA(21) crossover confirmed by RSI
recovering out of oversold territory; enters short on the mirror image.
Requires BOTH conditions, not either alone -- an EMA crossover alone fires
constantly in choppy markets, and RSI alone says nothing about trend
direction; the two together are why this is a distinct strategy from
Momentum, not a duplicate of it.
"""

from __future__ import annotations

from app.strategies.base import MarketSnapshot, Signal
from app.strategies.indicators import ema, rsi

FAST_SPAN = 9
SLOW_SPAN = 21
RSI_PERIOD = 14
OVERSOLD = 30.0
OVERBOUGHT = 70.0
DEFAULT_QTY = 10


class AlphaRSIEMAStrategy:
    key = "alpha_rsi_ema"
    name = "Alpha (RSI + EMA crossover)"

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        prices = market.prices
        if len(prices) < max(SLOW_SPAN, RSI_PERIOD) + 2:
            return None

        fast, slow = ema(prices, FAST_SPAN), ema(prices, SLOW_SPAN)
        r = rsi(prices, RSI_PERIOD)
        if any(v != v for v in (fast[-1], fast[-2], slow[-1], slow[-2], r[-1])):  # NaN check without importing math/np again
            return None

        crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
        crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]

        if crossed_up and r[-1] > OVERSOLD:
            return Signal("buy", DEFAULT_QTY, "market", None,
                          f"EMA{FAST_SPAN}/EMA{SLOW_SPAN} bullish crossover, RSI={r[-1]:.1f} confirms recovery from oversold")
        if crossed_down and r[-1] < OVERBOUGHT:
            return Signal("sell", DEFAULT_QTY, "market", None,
                          f"EMA{FAST_SPAN}/EMA{SLOW_SPAN} bearish crossover, RSI={r[-1]:.1f} confirms pullback from overbought")
        return None
