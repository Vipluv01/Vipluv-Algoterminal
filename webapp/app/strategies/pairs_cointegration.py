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
import pandas as pd
from statsmodels.tsa.stattools import coint

from app.strategies.base import Signal
from app.strategies.kalman import KalmanBetaAlpha

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
    # The actual held quantity on the A leg when position != "none", 0
    # otherwise. Optional/defaulted because pairs_cointegration.py below
    # never needs it (it always sizes with the same fixed self.qty on
    # both entry and close, so there's nothing to look up) -- but a
    # strategy whose entry size VARIES (pairs_kelly.py) needs it to close
    # exactly what it opened, not a fresh size guessed at close time. Same
    # "caller passes state in, strategy doesn't hide it" discipline this
    # class's own docstring establishes for `position` itself.
    position_qty_a: int = 0


@dataclass(frozen=True)
class PairSignal:
    signal_a: Signal | None
    signal_b: Signal | None
    new_position: PairPosition
    cointegration_pvalue: float
    zscore: float
    hedge_ratio: float


@dataclass(frozen=True)
class PairStats:
    """A read-only snapshot of the pair's current statistics, for display
    rather than trading -- everything a Pair Overview/Analytics page needs
    (live z-score, hedge ratio, cointegration status, correlation) without
    having to fire evaluate_pair() to get a value back, which returns None
    on the vast majority of ticks where no entry/exit/stop condition is
    met."""

    hedge_ratio: float
    cointegration_pvalue: float
    is_cointegrated: bool
    correlation: float
    spread: float
    zscore: float
    zscore_series: list[float | None]
    hedge_ratio_series: list[float]
    spread_series: list[float]


def compute_pair_stats(
    prices_a: np.ndarray, prices_b: np.ndarray, *,
    zscore_window: int = 60, coint_pvalue_max: float = 0.05,
    min_history: int = 90, series_length: int = 300,
) -> PairStats | None:
    """Deliberately a SEPARATE function from evaluate_pair, not a shared
    helper it also calls: evaluate_pair runs on every tick for every
    enabled allocation (app/strategy_runner.py's tick loop), so its cost is
    load-bearing, while this is only ever called on-demand when a user
    actually opens a display page. Computing the full rolling z-score
    SERIES below (pandas .rolling, needed for a chart) on every trading
    tick would be pure waste evaluate_pair has no use for -- keeping this
    separate keeps that cost off the hot path. The Kalman/cointegration
    math itself is intentionally duplicated (a handful of lines), not
    factored out, to avoid coupling the live trading path's performance to
    a display feature's needs.
    """
    a, b = prices_a, prices_b
    if len(a) < min_history or len(a) != len(b):
        return None

    kf = KalmanBetaAlpha()
    betas = np.empty(len(a))
    alphas = np.empty(len(a))
    for i in range(len(a)):
        betas[i], alphas[i] = kf.update(b[i], a[i])
    # Spread is the regression RESIDUAL (a - beta*b - alpha), not the raw
    # a - beta*b: now that the filter tracks an intercept, the residual is
    # what should actually be mean-reverting around zero, and dropping
    # alpha here would silently reintroduce the origin-passes-through-zero
    # assumption the intercept exists to remove.
    spread = a - (betas * b + alphas)

    _, pvalue, _ = coint(a, b)
    correlation = float(np.corrcoef(a, b)[0, 1])

    spread_s = pd.Series(spread)
    rolling_mean = spread_s.rolling(zscore_window).mean()
    rolling_std = spread_s.rolling(zscore_window).std(ddof=0)
    zseries = ((spread_s - rolling_mean) / rolling_std).to_numpy()

    window = spread[-zscore_window:]
    std = window.std(ddof=0)
    zscore = float((spread[-1] - window.mean()) / std) if std != 0 else float("nan")

    tail = slice(-series_length, None)
    return PairStats(
        hedge_ratio=float(betas[-1]),
        cointegration_pvalue=float(pvalue),
        # statsmodels' coint() returns a plain python float for a CLIPPED
        # extreme p-value but a numpy.float64 otherwise (confirmed directly
        # -- not a guess) -- comparing a numpy.float64 to a python float
        # yields numpy.bool_, which FastAPI's jsonable_encoder cannot
        # serialize. bool(...) here is load-bearing, not decorative: found
        # live when a strongly-cointegrated synthetic test pair (whose
        # clipped p-value happens to already be a plain float) hid this
        # exact bug, and only real, less-extreme market data surfaced it.
        is_cointegrated=bool(pvalue <= coint_pvalue_max),
        correlation=correlation,
        spread=float(spread[-1]),
        zscore=zscore,
        zscore_series=[None if np.isnan(z) else float(z) for z in zseries[tail]],
        hedge_ratio_series=betas[tail].tolist(),
        spread_series=spread[tail].tolist(),
    )


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

        # Kalman hedge ratio is computed unconditionally, even when the pair
        # turns out not to be cointegrated below -- an existing open
        # position still needs a real beta to unwind the B leg correctly by
        # (not the equal-quantity bug fixed above), so "not cointegrated
        # anymore" must still be able to size a proper close.
        kf = KalmanBetaAlpha()
        betas = np.empty(len(a))
        alphas = np.empty(len(a))
        for i in range(len(a)):
            betas[i], alphas[i] = kf.update(b[i], a[i])
        spread = a - (betas * b + alphas)  # regression residual -- see compute_pair_stats
        beta = float(betas[-1])

        # Engle-Granger cointegration test on the FULL available history --
        # this is the check the old Algo Terminal skipped, using correlation
        # (0.74) as a stand-in for it. Correlated random walks have no
        # stable spread; a low cointegration p-value is the actual evidence
        # a stable, tradeable equilibrium relationship exists at all.
        #
        # NOT YET DONE, flagged for a future pass: this should become a
        # TRAILING window, not the full history -- and for a statistical
        # reason, not just the O(n^2)-ish per-bar cost (measured: a test
        # sweep at this went 5.5ms -> 76.2ms -> 499ms as history length
        # grew, superlinearly). KalmanBetaAlpha exists specifically because
        # the hedge ratio is time-varying -- that is the whole justification
        # for a filter over a single OLS fit. Handing coint() the entire
        # session's history asks it to assume the relationship has been
        # stationary since the session began, which contradicts the
        # time-varying-beta premise the filter above already commits to. A
        # trailing window fixes both at once, and is standard on real pairs
        # desks for the same reason: a cointegration relationship estimated
        # over a stale multi-hour window is often statistically WORSE than
        # one estimated over a recent one, not merely slower to compute.
        _, pvalue, _ = coint(a, b)
        if pvalue > self.coint_pvalue_max:
            # Not cointegrated right now -- force-flat rather than silently
            # holding a position whose statistical justification just
            # disappeared.
            if pair.position != "none":
                return self._close(pair, pvalue, zscore=float("nan"), beta=beta)
            return None

        window = spread[-self.zscore_window:]
        mean, std = window.mean(), window.std(ddof=0)
        if std == 0:
            return None
        z = (spread[-1] - mean) / std

        if pair.position == "none":
            if z >= self.entry_z:
                return self._enter("short_spread", pair, pvalue, z, beta)
            if z <= -self.entry_z:
                return self._enter("long_spread", pair, pvalue, z, beta)
            return None

        # Already in a position: stop-loss takes priority over a normal exit.
        if abs(z) >= self.stop_z:
            return self._close(pair, pvalue, z, beta)
        reverted = (pair.position == "long_spread" and z >= self.exit_z) or \
                   (pair.position == "short_spread" and z <= self.exit_z)
        if reverted:
            return self._close(pair, pvalue, z, beta)
        return None

    def _leg_b_qty(self, beta: float) -> int:
        """The B leg must be sized in proportion to the current hedge ratio,
        not equal to leg A's quantity -- verified directly against a real
        example (icici_mean_reversion's own manual-trade screen showed
        ICICI 77 sh / HDFC 151 sh at beta=1.9564; 77 * 1.9564 ~= 151). Equal
        quantities on both legs would leave the position NOT
        dollar/beta-neutral, meaning it's exposed to the pair's shared
        market-wide moves rather than purely to the spread -- defeating the
        entire point of trading a hedged pair instead of two single stocks.
        Minimum 1 share so a very small beta never rounds a real leg away
        to zero.
        """
        return max(1, round(self.qty * beta))

    def _enter(self, direction: PairPosition, pair: PairSnapshot, pvalue: float, z: float, beta: float) -> PairSignal:
        qty_b = self._leg_b_qty(beta)
        if direction == "long_spread":
            sig_a = Signal("buy", self.qty, "market", None, f"pairs entry: z={z:.2f} <= -{self.entry_z}, long spread")
            sig_b = Signal("sell", qty_b, "market", None, f"pairs entry: z={z:.2f} <= -{self.entry_z}, short {pair.symbol_b} leg, beta={beta:.4f}")
        else:
            sig_a = Signal("sell", self.qty, "market", None, f"pairs entry: z={z:.2f} >= {self.entry_z}, short spread")
            sig_b = Signal("buy", qty_b, "market", None, f"pairs entry: z={z:.2f} >= {self.entry_z}, long {pair.symbol_b} leg, beta={beta:.4f}")
        return PairSignal(sig_a, sig_b, direction, pvalue, z, beta)

    def _close(self, pair: PairSnapshot, pvalue: float, zscore: float, beta: float) -> PairSignal:
        # Closing is the mirror image of whichever side is currently open --
        # same beta-scaled B-leg quantity used to open it, so the position
        # is fully unwound rather than leaving a residual on either leg.
        qty_b = self._leg_b_qty(beta)
        if pair.position == "long_spread":
            sig_a = Signal("sell", self.qty, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing long spread")
            sig_b = Signal("buy", qty_b, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing {pair.symbol_b} leg")
        else:
            sig_a = Signal("buy", self.qty, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing short spread")
            sig_b = Signal("sell", qty_b, "market", None, f"pairs exit/stop: z={zscore:.2f}, closing {pair.symbol_b} leg")
        return PairSignal(sig_a, sig_b, "none", pvalue, zscore, beta)
