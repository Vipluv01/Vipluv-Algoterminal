"""Pairs Trading (cointegration + Kalman-filtered hedge ratio) -- the 5th
strategy, ported from Vipluv's own icici_mean_reversion repo (ICICI vs
HDFC on NSE, Sharpe 1.74 / 73.6% win rate / -6.78% max drawdown in that
repo's own walk-forward-validated backtest). This is a from-scratch
reimplementation of that repo's documented methodology against THIS
project's Strategy interface, not a copy-paste -- the statistical approach
(Engle-Granger cointegration, Kalman-filtered dynamic hedge ratio, z-score
entry/exit/stop) is deliberately the same, because it's already validated
work, not because the code was lifted.

Deliberately NOT the same "mean reversion" as mean_reversion_bb.py: that
one fades ONE instrument back toward its own rolling mean; this one trades
the SPREAD between TWO cointegrated instruments back toward its
equilibrium. A single instrument being mean-reverting says nothing about
whether its price level is "wrong" -- a cointegrated pair's SPREAD reverting
is the actual, testable statistical claim (Engle-Granger below is what
tests it, rather than assuming it).

Unlike the other 4 strategies, this one is deliberately NOT stateless about
position -- entry vs. exit vs. stop-loss are three different actions on the
SAME open spread position, and there is no way to choose between them
without knowing whether one is already open. Rather than hide that as
mutable state inside the strategy object (which would make it much harder
to unit test and would put position-tracking in two places at once, the
same class of bug already caught once while building the live demo's order
tracking), the caller passes current position state in explicitly -- the
execution layer already has to track this anyway, from filled orders, to
answer "what does this user's book actually look like".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from statsmodels.tsa.stattools import coint

from app.strategies.base import Signal
from app.strategies.kalman import KalmanHedgeRatio

PairPosition = Literal["none", "long_spread", "short_spread"]
# long_spread  = long symbol_a, short symbol_b (bet the spread rises back up)
# short_spread = short symbol_a, long symbol_b (bet the spread falls back down)


@dataclass(frozen=True)
class PairSnapshot:
    symbol_a: str
    symbol_b: str
    prices_a: np.ndarray
    prices_b: np.ndarray
    position: PairPosition = "none"


@dataclass(frozen=True)
class PairSignal:
    signal_a: Signal | None
    signal_b: Signal | None
    new_position: PairPosition
    cointegration_pvalue: float
    zscore: float


class PairsCointegrationStrategy:
    key = "pairs_cointegration"
    name = "Pairs Trading (cointegration + Kalman hedge ratio)"

    def __init__(
        self,
        *,
        entry_z: float = 1.5,
        exit_z: float = 0.0,
        stop_z: float = 3.0,
        coint_pvalue_max: float = 0.05,
        zscore_window: int = 60,
        min_history: int = 90,
        qty: int = 10,
    ):
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.stop_z = stop_z
        self.coint_pvalue_max = coint_pvalue_max
        self.zscore_window = zscore_window
        self.min_history = min_history
        self.qty = qty

    def evaluate_pair(self, pair: PairSnapshot) -> PairSignal | None:
        a, b = pair.prices_a, pair.prices_b
        if len(a) < self.min_history or len(a) != len(b):
            return None

        # Engle-Granger cointegration test on the FULL available history --
        # this is the check the old Algo Terminal skipped, using correlation
        # (0.74) as a stand-in for it. Correlated random walks have no
        # stable spread; a low cointegration p-value is the actual evidence
        # a stable, tradeable equilibrium relationship exists at all.
        _, pvalue, _ = coint(a, b)
        if pvalue > self.coint_pvalue_max:
            # Not cointegrated right now -- force-flat rather than silently
            # holding a position whose statistical justification just
            # disappeared.
            if pair.position != "none":
                return self._close(pair, pvalue, zscore=float("nan"))
            return None

        kf = KalmanHedgeRatio()
        betas = np.empty(len(a))
        for i in range(len(a)):
            betas[i] = kf.update(a[i], b[i])
        spread = a - betas * b

        window = spread[-self.zscore_window:]
        mean, std = window.mean(), window.std(ddof=0)
        if std == 0:
            return None
        z = (spread[-1] - mean) / std

        if pair.position == "none":
            if z >= self.entry_z:
                return self._enter("short_spread", pair, pvalue, z)
            if z <= -self.entry_z:
                return self._enter("long_spread", pair, pvalue, z)
            return None

        # Already in a position: stop-loss takes priority over a normal exit.
        if abs(z) >= self.stop_z:
            return self._close(pair, pvalue, z)
        reverted = (pair.position == "long_spread" and z >= self.exit_z) or \
                   (pair.position == "short_spread" and z <= self.exit_z)
        if reverted:
            return self._close(pair, pvalue, z)
        return None

    def _enter(self, direction: PairPosition, pair: PairSnapshot, pvalue: float, z: float) -> PairSignal:
        if direction == "long_spread":
            sig_a = Signal("buy", self.qty, "market", None, f"pairs entry: z={z:.2f} <= -{self.entry_z}, long spread")
            sig_b = Signal("sell", self.qty, "market", None, f"pairs entry: z={z:.2f} <= -{self.entry_z}, short {pair.symbol_b} leg")
        else:
            sig_a = Signal("sell", self.qty, "market", None, f"pairs entry: z={z:.2f} >= {self.entry_z}, short spread")
            sig_b = Signal("buy", self.qty, "market", None, f"pairs entry: z={z:.2f} >= {self.entry_z}, long {pair.symbol_b} leg")
        return PairSignal(sig_a, sig_b, direction, pvalue, z)

    def _close(self, pair: PairSnapshot, pvalue: float, zscore: float) -> PairSignal:
        # Closing is the mirror image of whichever side is currently open.
        if pair.position == "long_spread":
            sig_a = Signal("sell", self.qty, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing long spread")
            sig_b = Signal("buy", self.qty, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing {pair.symbol_b} leg")
        else:
            sig_a = Signal("buy", self.qty, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing short spread")
            sig_b = Signal("sell", self.qty, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing {pair.symbol_b} leg")
        return PairSignal(sig_a, sig_b, "none", pvalue, zscore)
