"""3-asset banking basket, mean-reverting around a Johansen-derived
cointegrating combination -- the natural generalization of pairs_
cointegration.py's 2-series Engle-Granger approach to N series.

Johansen, not Engle-Granger, because Engle-Granger only tests "is there
SOME cointegrating relationship between exactly two series" and needs one
series picked as the dependent variable; extending it to 3 symbols would
mean either three separate pairwise tests (which is a different, weaker
claim than "these three move together") or arbitrarily choosing which
symbol is "dependent." Johansen's eigenvectors instead directly estimate
the linear combination of ALL THREE series that is most strongly
stationary -- exactly what a basket spread needs.

Basket: ICICIBANK, HDFCBANK, SBIN -- three large private/public-sector NSE
banks, the same asset class pairs_cointegration.py already trades a 2-name
slice of, extended to a third name in the same sector rather than an
unrelated symbol, since sector-level co-movement is the actual economic
reason to expect a stable relationship in the first place.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from statsmodels.tsa.vector_ar.vecm import coint_johansen

from app.strategies.base import Signal
from app.strategies.pairs_cointegration import PairPosition

BASKET_SYMBOLS: tuple[str, ...] = ("ICICIBANK", "HDFCBANK", "SBIN")


@dataclass(frozen=True)
class BasketSnapshot:
    symbols: tuple[str, ...]
    prices: dict[str, np.ndarray]
    position: PairPosition = "none"
    # Actual held qty per leg when position != "none" -- unlike pairs_
    # kelly.py's entry sizing, this strategy always sizes with the SAME
    # fixed self.qty (scaled by weight) at both entry and close, so an
    # exact quantity round-trip isn't at risk here the way it is for a
    # variably-sized strategy. Kept for interface symmetry with
    # PairSnapshot, not because this strategy currently needs it.
    position_qtys: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BasketSignal:
    leg_signals: dict[str, Signal]
    new_position: PairPosition
    is_cointegrated: bool
    zscore: float
    weights: dict[str, float]


def _johansen_top_eigenvector(prices_matrix: np.ndarray) -> tuple[bool, np.ndarray]:
    """Runs Johansen on the full N-series price matrix and returns
    (is_cointegrated, weights) where weights is the eigenvector for the
    LARGEST eigenvalue -- the single strongest cointegrating relationship
    -- normalized so the first symbol's weight is exactly 1 (the basket
    spread is expressed "per unit of the first symbol," the same
    convention pairs_cointegration.py's spread = a - beta*b uses).

    is_cointegrated is the rank<=0 null REJECTED at 95% (see
    app/quant/stationarity.py's johansen_test docstring for the identical
    convention on the 2-series case) -- at least one cointegrating
    relationship exists among the three series, not just correlation.
    """
    with warnings.catch_warnings():
        # See app/quant/stationarity.py's johansen_test for why this
        # specific warning is suppressed: a real, verified-harmless numpy
        # complex-to-real cast inside statsmodels' eigenvalue solver, not
        # a sign of a wrong result.
        warnings.simplefilter("ignore", category=np.exceptions.ComplexWarning)
        result = coint_johansen(prices_matrix, det_order=0, k_ar_diff=1)

    trace_stat_r0 = float(result.lr1[0])
    crit_95_r0 = float(result.cvt[0][1])
    is_cointegrated = trace_stat_r0 > crit_95_r0

    evec = np.real(result.evec[:, 0])
    if evec[0] == 0:
        return is_cointegrated, evec  # degenerate; caller's std==0 guard downstream will skip this bar
    weights = evec / evec[0]
    return is_cointegrated, weights


@dataclass
class MultiBasketStrategy:
    key: str = "multi_basket"
    name: str = "Multi-Asset Banking Basket (Johansen)"
    symbols: tuple[str, ...] = BASKET_SYMBOLS
    entry_z: float = 1.5
    exit_z: float = 0.0
    stop_z: float = 3.0
    zscore_window: int = 60
    min_history: int = 150  # Johansen over 3 series wants more data than a
                              # 2-series Engle-Granger test to be reliable
    qty: int = 10

    def evaluate_basket(self, snapshot: BasketSnapshot) -> BasketSignal | None:
        if snapshot.symbols != self.symbols:
            raise ValueError(f"MultiBasketStrategy trades {self.symbols}, got snapshot for {snapshot.symbols}")

        lengths = {len(snapshot.prices[s]) for s in self.symbols}
        if len(lengths) != 1 or next(iter(lengths)) < self.min_history:
            return None

        prices_matrix = np.column_stack([snapshot.prices[s] for s in self.symbols])
        is_cointegrated, weights_arr = _johansen_top_eigenvector(prices_matrix)
        weights = dict(zip(self.symbols, weights_arr))

        if not is_cointegrated:
            if snapshot.position != "none":
                return self._close(snapshot, weights, z=float("nan"), is_cointegrated=False)
            return None

        spread = prices_matrix @ weights_arr
        window = spread[-self.zscore_window:]
        mean, std = window.mean(), window.std(ddof=0)
        if std == 0:
            return None
        z = float((spread[-1] - mean) / std)

        if snapshot.position == "none":
            if z >= self.entry_z:
                return self._enter("short_spread", weights, z)
            if z <= -self.entry_z:
                return self._enter("long_spread", weights, z)
            return None

        if abs(z) >= self.stop_z:
            return self._close(snapshot, weights, z, True)
        reverted = (snapshot.position == "long_spread" and z >= self.exit_z) or \
                   (snapshot.position == "short_spread" and z <= self.exit_z)
        if reverted:
            return self._close(snapshot, weights, z, True)
        return None

    def _leg_side_and_qty(self, direction: PairPosition, symbol: str, weights: dict[str, float]) -> tuple[str, int]:
        """direction="long_spread" means betting spread = sum(w_i * p_i)
        RISES: buy every leg with a positive weight, sell every leg with a
        negative weight (a positive-weight leg's own price rising is what
        makes the spread rise; a negative-weight leg's price FALLING is
        what makes the spread rise, so being short it profits from that
        exact overall bet). "short_spread" is the mirror image."""
        w = weights[symbol]
        long_spread = direction == "long_spread"
        positive_weight = w > 0
        buy = long_spread == positive_weight
        side = "buy" if buy else "sell"
        qty = max(1, round(self.qty * abs(w)))
        return side, qty

    def _enter(self, direction: PairPosition, weights: dict[str, float], z: float) -> BasketSignal:
        leg_signals = {}
        for sym in self.symbols:
            side, qty = self._leg_side_and_qty(direction, sym, weights)
            leg_signals[sym] = Signal(
                side, qty, "market", None,
                f"multi_basket entry: z={z:.2f}, {direction}, weight={weights[sym]:.4f}",
            )
        return BasketSignal(leg_signals=leg_signals, new_position=direction, is_cointegrated=True, zscore=z, weights=weights)

    def _close(self, snapshot: BasketSnapshot, weights: dict[str, float], z: float, is_cointegrated: bool) -> BasketSignal:
        opposite: PairPosition = "short_spread" if snapshot.position == "long_spread" else "long_spread"
        leg_signals = {}
        for sym in self.symbols:
            side, qty = self._leg_side_and_qty(opposite, sym, weights)
            leg_signals[sym] = Signal(
                side, qty, "market", None,
                f"multi_basket exit/stop: z={z:.2f}, closing {snapshot.position}",
            )
        return BasketSignal(leg_signals=leg_signals, new_position="none", is_cointegrated=is_cointegrated, zscore=z, weights=weights)
