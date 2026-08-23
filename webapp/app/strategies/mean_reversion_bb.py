"""Mean Reversion Bot: Bollinger Bands fade with RSI confirmation --
mirrors opensoft-2026/trading-bots' publicly-described strategy
(feature-level description only; independent implementation, no code read
or copied).

Named `mean_reversion_bb` (not just `mean_reversion`) specifically to stay
distinct from the 5th strategy (pairs_cointegration.py), which is ALSO a
mean-reversion strategy but of a structurally different kind: this one
fades a single instrument back toward its own recent rolling mean;
pairs_cointegration trades the SPREAD between two cointegrated instruments
back toward its equilibrium. Same word, different statistical object --
worth keeping the names unambiguous rather than letting "mean reversion"
collide.
"""

from __future__ import annotations

from app.strategies.base import MarketSnapshot, Signal
from app.strategies.indicators import bollinger_bands, rsi

BB_WINDOW = 20
RSI_PERIOD = 14
OVERSOLD = 30.0
OVERBOUGHT = 70.0
DEFAULT_QTY = 10


class MeanReversionBollingerStrategy:
    key = "mean_reversion_bb"
    name = "Mean Reversion (Bollinger fade + RSI confirmation)"

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        prices = market.prices
        if len(prices) < max(BB_WINDOW, RSI_PERIOD) + 1:
            return None

        lower, mid, upper = bollinger_bands(prices, BB_WINDOW)
        r = rsi(prices, RSI_PERIOD)
        if any(v != v for v in (lower[-1], upper[-1], r[-1])):
            return None

        price = prices[-1]
        if price <= lower[-1] and r[-1] < OVERSOLD:
            return Signal("buy", DEFAULT_QTY, "market", None,
                          f"price {price:.2f} at/below lower Bollinger band {lower[-1]:.2f}, RSI={r[-1]:.1f} confirms oversold -- fading back toward the mean")
        if price >= upper[-1] and r[-1] > OVERBOUGHT:
            return Signal("sell", DEFAULT_QTY, "market", None,
                          f"price {price:.2f} at/above upper Bollinger band {upper[-1]:.2f}, RSI={r[-1]:.1f} confirms overbought -- fading back toward the mean")
        return None
