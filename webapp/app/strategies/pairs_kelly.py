"""Pairs Trading, sized by Fractional Kelly instead of a fixed quantity.

Same statistical core as pairs_cointegration.py (Engle-Granger
cointegration + Kalman-filtered dynamic hedge ratio/intercept + z-score
entry/exit/stop) -- the difference is entirely in SIZING. pairs_
cointegration.py trades a fixed `self.qty` on every entry, the same size
whether its own recent record of similar setups has been mostly winners or
mostly losers. This strategy estimates win_rate/avg_win/avg_loss from a
scan of ITS OWN price history for past entry-threshold crossings and how
they resolved, then sizes through app.position_sizing.size_position --
the same fractional-Kelly machinery app/routers/risk.py already displays,
now actually driving an order size rather than sitting unused (Phase 1
found Kelly fully implemented and tested but never called by any execution
path; this is the first strategy that calls it).

The historical scan is self-contained (uses only prices_a/prices_b, which
this strategy already receives in full) rather than requiring the caller
to inject trade history through PairSnapshot -- keeping the exact same
evaluate_pair(PairSnapshot) -> PairSignal|None shape pairs_cointegration.py
uses, so PairsAdapter and strategy_runner.py's live dispatch don't need a
second code path for this strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from statsmodels.tsa.stattools import coint

from app.position_sizing import size_position
from app.strategies.base import Signal
from app.strategies.kalman import KalmanBetaAlpha
from app.strategies.pairs_cointegration import PairPosition, PairSignal, PairSnapshot

# The account value Kelly sizes against. Strategies here have no real
# account awareness (app/strategies/base.py's own docstring: a strategy
# "does not know about a user's existing position or cash") -- pairs_
# cointegration.py's answer to that gap is a fixed qty=10; this strategy's
# answer is a fixed assumed notional, which is the input Kelly sizing
# itself needs a currency amount for. Same category of simplification,
# consistent with the rest of this strategy library, not a new one.
ASSUMED_ACCOUNT_VALUE = 100_000.0

MIN_HISTORICAL_TRADES = 5  # below this, a win-rate estimate is closer to
                             # noise than signal -- fall back to a single
                             # conservative default trade rather than size
                             # off 2 data points with false confidence


@dataclass(frozen=True)
class _HistoricalTradeStats:
    win_rate: float
    avg_win: float   # positive magnitude, in spread units
    avg_loss: float  # positive magnitude, in spread units
    n_trades: int


def _scan_historical_trades(
    zscore: np.ndarray, spread: np.ndarray, *, entry_z: float, exit_z: float, stop_z: float,
) -> _HistoricalTradeStats | None:
    """Walks the full z-score/spread history looking for every point it
    would have crossed the entry threshold, then follows forward to see
    whether that hypothetical trade would have hit its exit (win) or its
    stop (loss) first. This is a SIMPLE forward scan over data the
    strategy already has, not a full backtest (no fees, no sizing fed
    back in) -- it exists only to produce a win_rate/avg_win/avg_loss
    triple for Kelly, the same way a trader's own trade journal would.
    """
    n = len(zscore)
    trades: list[float] = []  # signed spread P&L per hypothetical trade
    i = 0
    while i < n:
        z = zscore[i]
        if np.isnan(z):
            i += 1
            continue
        if z >= entry_z:
            direction = -1  # short spread: profits if spread falls
        elif z <= -entry_z:
            direction = 1   # long spread: profits if spread rises
        else:
            i += 1
            continue

        entry_spread = spread[i]
        j = i + 1
        outcome = None
        while j < n:
            zj = zscore[j]
            if np.isnan(zj):
                j += 1
                continue
            reverted = (direction == 1 and zj >= exit_z) or (direction == -1 and zj <= exit_z)
            stopped = abs(zj) >= stop_z
            if stopped:
                outcome = direction * (spread[j] - entry_spread)
                break
            if reverted:
                outcome = direction * (spread[j] - entry_spread)
                break
            j += 1
        if outcome is not None:
            trades.append(outcome)
            i = j + 1
        else:
            break  # ran out of data before this hypothetical trade resolved

    if len(trades) < MIN_HISTORICAL_TRADES:
        return None

    trades_arr = np.array(trades)
    wins = trades_arr[trades_arr > 0]
    losses = trades_arr[trades_arr <= 0]
    if len(wins) == 0 or len(losses) == 0:
        # Kelly's formula needs BOTH a positive avg_win and a positive
        # avg_loss (see position_sizing.kelly_fraction -- it raises
        # otherwise); an all-wins or all-losses history can't estimate a
        # loss/win ratio at all, so there's nothing valid to compute yet.
        return None

    return _HistoricalTradeStats(
        win_rate=len(wins) / len(trades_arr),
        avg_win=float(wins.mean()),
        avg_loss=float(-losses.mean()),
        n_trades=len(trades_arr),
    )


@dataclass
class PairsKellyStrategy:
    key: str = "pairs_kelly"
    name: str = "Pairs Trading + Kelly"
    entry_z: float = 1.5
    exit_z: float = 0.0
    stop_z: float = 3.0
    coint_pvalue_max: float = 0.05
    zscore_window: int = 60
    min_history: int = 90
    kelly_multiplier: float = 0.25
    max_position_fraction: float = 0.5
    fallback_qty: int = 10  # used when there isn't enough historical-trade
                              # evidence yet (see MIN_HISTORICAL_TRADES) --
                              # same fixed size pairs_cointegration.py always uses

    def evaluate_pair(self, pair: PairSnapshot) -> PairSignal | None:
        a, b = pair.prices_a, pair.prices_b
        if len(a) < self.min_history or len(a) != len(b):
            return None

        kf = KalmanBetaAlpha()
        betas = np.empty(len(a))
        alphas = np.empty(len(a))
        for i in range(len(a)):
            betas[i], alphas[i] = kf.update(b[i], a[i])
        spread = a - (betas * b + alphas)
        beta = float(betas[-1])

        # Full-history coint() -- same NOT-YET-DONE trailing-window item as
        # pairs_cointegration.evaluate_pair (see the comment there for why
        # it's a statistical fix, not just a perf one).
        _, pvalue, _ = coint(a, b)
        if pvalue > self.coint_pvalue_max:
            if pair.position != "none":
                return self._close(pair, pvalue, zscore=float("nan"), beta=beta)
            return None

        window = spread[-self.zscore_window:]
        mean, std = window.mean(), window.std(ddof=0)
        if std == 0:
            return None
        z = (spread[-1] - mean) / std

        # Rolling z-score series over the SAME window used for the live z,
        # for the historical-trade scan below -- computed once here, not
        # duplicated per bar by evaluate_pair being called repeatedly
        # (pandas is already a dependency; reusing compute_pair_stats'
        # rolling approach keeps this consistent with it).
        import pandas as pd
        spread_s = pd.Series(spread)
        rolling_mean = spread_s.rolling(self.zscore_window).mean()
        rolling_std = spread_s.rolling(self.zscore_window).std(ddof=0)
        zscore_series = ((spread_s - rolling_mean) / rolling_std).to_numpy()

        qty_a = self._kelly_qty_a(zscore_series, spread, price=float(a[-1]))

        if pair.position == "none":
            if z >= self.entry_z:
                return self._enter("short_spread", pair, pvalue, z, beta, qty_a)
            if z <= -self.entry_z:
                return self._enter("long_spread", pair, pvalue, z, beta, qty_a)
            return None

        if abs(z) >= self.stop_z:
            return self._close(pair, pvalue, z, beta)
        reverted = (pair.position == "long_spread" and z >= self.exit_z) or \
                   (pair.position == "short_spread" and z <= self.exit_z)
        if reverted:
            return self._close(pair, pvalue, z, beta)
        return None

    def _kelly_qty_a(self, zscore_series: np.ndarray, spread: np.ndarray, *, price: float) -> int:
        stats = _scan_historical_trades(
            zscore_series, spread, entry_z=self.entry_z, exit_z=self.exit_z, stop_z=self.stop_z,
        )
        if stats is None:
            return self.fallback_qty
        result = size_position(
            win_rate=stats.win_rate, avg_win=stats.avg_win, avg_loss=stats.avg_loss,
            account_value=ASSUMED_ACCOUNT_VALUE, price=price,
            kelly_multiplier=self.kelly_multiplier, max_position_fraction=self.max_position_fraction,
        )
        # Kelly can legitimately size to zero (no edge found, or an edge
        # too small to clear even one share at this price) -- fall back to
        # the minimum tradeable size rather than submit a zero-qty order,
        # matching pairs_cointegration.py's own "minimum 1 share" floor on
        # its beta-scaled B leg for the identical reason.
        return max(1, result.qty)

    def _leg_b_qty(self, qty_a: int, beta: float) -> int:
        return max(1, round(qty_a * beta))

    def _enter(self, direction: PairPosition, pair: PairSnapshot, pvalue: float, z: float, beta: float, qty_a: int) -> PairSignal:
        qty_b = self._leg_b_qty(qty_a, beta)
        if direction == "long_spread":
            sig_a = Signal("buy", qty_a, "market", None, f"pairs_kelly entry: z={z:.2f} <= -{self.entry_z}, long spread, kelly qty={qty_a}")
            sig_b = Signal("sell", qty_b, "market", None, f"pairs_kelly entry: short {pair.symbol_b} leg, beta={beta:.4f}")
        else:
            sig_a = Signal("sell", qty_a, "market", None, f"pairs_kelly entry: z={z:.2f} >= {self.entry_z}, short spread, kelly qty={qty_a}")
            sig_b = Signal("buy", qty_b, "market", None, f"pairs_kelly entry: long {pair.symbol_b} leg, beta={beta:.4f}")
        return PairSignal(sig_a, sig_b, direction, pvalue, z, beta)

    def _close(self, pair: PairSnapshot, pvalue: float, zscore: float, beta: float) -> PairSignal:
        # Closing must unwind EXACTLY what was opened, not a freshly
        # Kelly-sized quantity -- entry sizing can legitimately differ
        # bar to bar as the historical win/loss record shifts, so re-
        # deriving a qty here would risk leaving a residual position open
        # (the same failure mode routers/pairs.py's own force_close
        # docstring calls out for beta drift, just for size instead of
        # hedge ratio). pair.position_qty_a is the caller's record of what
        # is ACTUALLY held; only fall back to fallback_qty if the caller
        # didn't supply it (position_qty_a == 0 with position != "none"
        # is a caller bug, but degrading to a fixed close is safer than
        # emitting a qty=0 order that closes nothing).
        qty_a = pair.position_qty_a if pair.position_qty_a > 0 else self.fallback_qty
        qty_b = self._leg_b_qty(qty_a, beta)
        if pair.position == "long_spread":
            sig_a = Signal("sell", qty_a, "market", None, f"pairs_kelly exit/stop: z={zscore:.2f}, closing long spread")
            sig_b = Signal("buy", qty_b, "market", None, f"pairs_kelly exit/stop: closing {pair.symbol_b} leg")
        else:
            sig_a = Signal("buy", qty_a, "market", None, f"pairs_kelly exit/stop: z={zscore:.2f}, closing short spread")
            sig_b = Signal("sell", qty_b, "market", None, f"pairs_kelly exit/stop: closing {pair.symbol_b} leg")
        return PairSignal(sig_a, sig_b, "none", pvalue, zscore, beta)
