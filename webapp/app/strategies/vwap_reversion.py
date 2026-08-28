"""Fades extreme deviations of price from session VWAP back toward it.

Distinct from mean_reversion_bb.py: that strategy fades price away from
its own rolling PRICE mean; this one fades price away from a
VOLUME-WEIGHTED mean, which reacts differently to a move that happens on
thin volume (barely shifts VWAP, so a small price move can already look
like a large deviation) versus one that happens on heavy volume (VWAP
itself moves toward the new price, so deviation stays small even for a
big move) -- VWAP is a genuinely different reference level, not a
relabeled moving average.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.strategies.base import MarketSnapshot, Signal
from app.strategies.indicators import vwap

DEVIATION_WINDOW = 30       # bars of recent deviation used to scale "extreme"
DEVIATION_THRESHOLD = 1.5   # in units of the recent deviation's own std
DEFAULT_QTY = 10


@dataclass
class VWAPReversionStrategy:
    key: str = "vwap_reversion"
    name: str = "VWAP Reversion"
    deviation_threshold: float = DEVIATION_THRESHOLD
    window: int = DEVIATION_WINDOW
    qty: int = DEFAULT_QTY

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        if market.volumes is None:
            # No volume data available -- VWAP is meaningless without it,
            # and returning None (not raising) matches this codebase's
            # "insufficient data yet" convention elsewhere (rsi/bollinger_
            # bands return NaN rather than error before their own warm-up).
            return None

        prices, volumes = market.prices, market.volumes
        if len(prices) < self.window + 1:
            return None

        vwap_series = vwap(prices, volumes)
        if np.isnan(vwap_series[-1]):
            return None

        deviation = prices - vwap_series
        recent = deviation[-self.window:]
        std = float(recent.std(ddof=0))
        if std == 0.0:
            return None

        z = float(deviation[-1] / std)
        if z >= self.deviation_threshold:
            return Signal(
                "sell", self.qty, "market", None,
                f"vwap fade: deviation z={z:.2f} >= {self.deviation_threshold} (price above session VWAP)",
            )
        if z <= -self.deviation_threshold:
            return Signal(
                "buy", self.qty, "market", None,
                f"vwap fade: deviation z={z:.2f} <= -{self.deviation_threshold} (price below session VWAP)",
            )
        return None
