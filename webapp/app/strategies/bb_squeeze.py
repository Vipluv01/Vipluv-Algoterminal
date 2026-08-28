"""Volatility-breakout, not mean-reversion: enters WITH a directional
break once Bollinger bandwidth has compressed into its own low-percentile
range (a "squeeze" -- the textbook precursor to an expansion move) and
price then breaks outside the current bands.

Deliberately the opposite trade direction from mean_reversion_bb.py, which
FADES a band touch expecting reversion. This strategy exists because a
band touch after a genuine volatility contraction is a different
statistical situation than a band touch during ordinary choppy trading --
conflating the two into one "trade the bands" strategy would mean trading
against the move exactly when a real breakout is most likely underway.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.strategies.base import MarketSnapshot, Signal
from app.strategies.indicators import bollinger_bands, bollinger_bandwidth

BB_PERIOD = 20
BB_NUM_STD = 2.0
SQUEEZE_LOOKBACK = 120       # bars of bandwidth history the percentile is measured against
SQUEEZE_PERCENTILE = 10.0    # "compressed" means bandwidth was in its own bottom 10%
DEFAULT_QTY = 10


@dataclass
class BBSqueezeStrategy:
    key: str = "bb_squeeze"
    name: str = "Bollinger Band Squeeze Breakout"
    period: int = BB_PERIOD
    num_std: float = BB_NUM_STD
    squeeze_lookback: int = SQUEEZE_LOOKBACK
    squeeze_percentile: float = SQUEEZE_PERCENTILE
    qty: int = DEFAULT_QTY

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        prices = market.prices
        if len(prices) < self.squeeze_lookback + self.period:
            return None

        bandwidth = bollinger_bandwidth(prices, period=self.period, num_std=self.num_std)
        recent_bw = bandwidth[-self.squeeze_lookback:]
        valid = recent_bw[~np.isnan(recent_bw)]
        if len(valid) < self.squeeze_lookback // 2:
            return None

        # The bar just BEFORE this one must have been inside a squeeze --
        # checking the current bar's own bandwidth would be circular once
        # a breakout has already widened it back out.
        prev_bandwidth = bandwidth[-2]
        if np.isnan(prev_bandwidth):
            return None
        squeeze_threshold = float(np.percentile(valid, self.squeeze_percentile))
        was_squeezed = prev_bandwidth <= squeeze_threshold
        if not was_squeezed:
            return None

        lower, mid, upper = bollinger_bands(prices, window=self.period, n_std=self.num_std)
        if np.isnan(upper[-1]) or np.isnan(lower[-1]):
            return None

        if prices[-1] > upper[-1]:
            return Signal(
                "buy", self.qty, "market", None,
                f"bb squeeze breakout up: bandwidth {prev_bandwidth:.4f} was <= p{self.squeeze_percentile:.0f} "
                f"({squeeze_threshold:.4f}), price broke above the upper band",
            )
        if prices[-1] < lower[-1]:
            return Signal(
                "sell", self.qty, "market", None,
                f"bb squeeze breakout down: bandwidth {prev_bandwidth:.4f} was <= p{self.squeeze_percentile:.0f} "
                f"({squeeze_threshold:.4f}), price broke below the lower band",
            )
        return None
