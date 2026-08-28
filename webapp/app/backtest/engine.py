"""Replays a strategy over a generated price path and scores it.

Fills go through the EXACT SAME functions the live app uses for its own
P&L (app.accounting._walk_fills/compute_realizations, app.dashboard_stats.
compute_trade_stats) -- backtest and live accounting share one
implementation of "what does a fill mean," not two that could quietly
drift apart. Orders here are real (transient, never persisted or added to
a session) app.models.trading.Order instances for exactly that reason:
using the real model, not a lookalike dataclass, is what makes "identical
state engine" a literal fact rather than a design intention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app.accounting import _walk_fills, compute_realizations
from app.backtest.adapters import BacktestStrategy
from app.backtest.paths import MultiAssetHistory
from app.dashboard_stats import compute_trade_stats
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side

# A bar here is one MultiAssetHistory step, i.e. one registry.step_all()
# call in app/backtest/paths.py's generate_market_paths -- which is one
# simulated SECOND, not one trading day. This was a real bug: annualizing
# with sqrt(252) (days) silently treated a 500-bar backtest as if it
# covered 500 TRADING DAYS (two real years) rather than 500 simulated
# seconds (eight minutes), understating every realized-vol figure derived
# from it by roughly two orders of magnitude and, separately, giving Sharpe/
# Calmar the wrong annualization scale entirely.
#
# 252 * 6.5 * 3600 is not a new number invented for this fix -- it's the
# EXACT value sim/bourse_sim/fundamental.py's own FundamentalProcess already
# hardcodes as `dt = 1/(252*6.5*3600)` ("one simulated second, ~trading-year
# units"), and it's also the SAME constant OptionsBacktestAdapter's own
# BACKTEST_BARS_PER_YEAR now uses (app/backtest/adapters.py) -- both
# domains that consume these bars share exactly one clock, not two that can
# silently disagree the way this bug and the Phase-5.5 options-pricing bug
# both were, independently, before being fixed.
BARS_PER_YEAR = 252 * 6.5 * 3600  # NSE trading seconds/year: 5,896,800
_EPOCH = datetime(2000, 1, 1)  # arbitrary fixed origin -- only relative
                                 # ordering matters, _walk_fills just needs
                                 # created_at to sort correctly

# An annualized Sharpe or Calmar beyond this magnitude is not a real
# trading result -- it's what a near-deterministic (near-zero-variance)
# return series produces once divided by its own tiny denominator and
# annualized, not genuine risk-adjusted skill. Found directly: a delta-
# hedged options strategy's mark-to-market P&L, dominated by smooth theta
# decay against an underlying that barely moves, produced a raw (pre-
# annualization) mean/std ratio of ~168 on one backtest path -- physically
# impossible for a real strategy (a raw per-bar Sharpe of 168 implies
# near-certainty on every single bar).
#
# This bound is on the ANNUALIZED value itself, not on the raw per-bar
# ratio -- it does NOT need to be rescaled when BARS_PER_YEAR changes, and
# an earlier version of this comment's own worked example (alpha_rsi_ema's
# "-0.22 raw / ~-3.5 annualized") was measured under the pre-fix sqrt(252)
# annualization and is stale; under the corrected BARS_PER_YEAR that same
# -0.22 raw now annualizes to roughly -534, which this bound correctly
# rejects. That is not this bound being miscalibrated -- see
# sim/KNOWN_ISSUES.md's "most equity strategy backtests are fee-dominated,
# not volatility-limited" section: most of this project's strategies really
# do produce a near-deterministic result at current turnover and fee_bps
# (measured: fee cost is 92-103% of realized P&L across 500-20,000 bars,
# flat regardless of horizon), so this bound rejecting them is it working
# as designed, not a bug to tune away. Do not raise this bound to let more
# strategies clear it, and do not lower fee_bps to shrink their fee drag --
# both would hide the same real finding rather than fix anything.
MAX_PLAUSIBLE_ANNUALIZED_RATIO = 10.0


@dataclass(frozen=True)
class BacktestRunResult:
    strategy_key: str
    seed: int
    steps: int
    sharpe_ratio: float | None       # None when there's no valid risk-adjusted
    sharpe_invalid_reason: str | None  # number to report -- see MAX_PLAUSIBLE_ANNUALIZED_RATIO
    win_rate: float | None           # None when there were zero closed trades
    profit_factor: float | None      # None when there were zero losing trades
    max_drawdown: float              # fraction, e.g. 0.15 == 15% -- always well-defined, never None
    calmar_ratio: float | None
    calmar_invalid_reason: str | None
    # Two DELIBERATELY separate counts, not one "trade_count" -- a strategy
    # holding one open position for the entire path submits real orders
    # (an entry, at minimum) but realizes zero ROUND TRIPS, and reporting
    # that as "0 trades" while equity visibly moved is misleading. See
    # compute_realizations vs raw Order count: round_trips_closed counts
    # CLOSING/reducing fills (what win_rate/profit_factor are computed
    # over); orders_submitted counts every Order this backtest actually
    # created, opens included. Never divide a performance ratio by either
    # without checking it's nonzero first.
    orders_submitted: int
    round_trips_closed: int
    equity_curve: np.ndarray         # one value per bar, mark-to-market
    initial_cash: float
    final_equity: float


def _resolve_price(strategy: BacktestStrategy, path: MultiAssetHistory, symbol: str, step: int) -> float:
    """The bar-`step` price for `symbol` -- normally a straight lookup
    into the pre-generated path (path.paths[symbol].close[step]), the
    assumption every strategy shape used before this phase. A strategy
    trading SYNTHETIC option contracts (app/backtest/adapters.
    OptionsBacktestAdapter) has no such pre-generated path to look up --
    an option contract key is never one of the finitely-many pre-generated
    equity symbols (there are unboundedly many possible strikes), and its
    price is BSM-model-computed, not path-observed, by construction (see
    app/options/execution.py). Such a strategy instead implements an
    optional `mark_price(symbol, history, step) -> float | None` method;
    when present and it returns a real number for this symbol, that value
    wins. Every OTHER strategy adapter has no such method at all (getattr
    returns None), so this is a no-op extension for all 8 pre-existing
    strategies -- their behavior is byte-for-byte unchanged.
    """
    resolver = getattr(strategy, "mark_price", None)
    if resolver is not None:
        price = resolver(symbol, path, step)
        if price is not None:
            return float(price)
    return float(path.paths[symbol].close[step])


def _build_orders(strategy: BacktestStrategy, path: MultiAssetHistory, fee_bps: float) -> tuple[list[Order], list[int]]:
    """Steps through every bar, asks the strategy what it wants, and turns
    each desired trade into a fully-filled Order at that bar's close price
    plus a fee/slippage drag.

    Fills unconditionally at the bar's close, no partial fills or
    rejections: there is no real order book on the backtest side to match
    against (the PRICE path already came from one, when it was generated
    -- see paths.py) -- this is the standard walk-forward-backtest
    assumption that a strategy's own trades don't move the market it's
    being evaluated against, same as bourse's own icici_mean_reversion
    reference backtest this strategy library is validated against.

    fee_bps is a real cost, not decorative: it worsens the effective fill
    price in the direction that hurts the trader (higher for a buy, lower
    for a sell), so a strategy that churns frequently for little real edge
    shows that cost directly in its realized P&L, not just in a separate
    "fees paid" line nobody looks at.
    """
    orders: list[Order] = []
    order_steps: list[int] = []  # order_steps[i] is the bar orders[i] was submitted on --
                                   # tracked directly at construction time rather than
                                   # reverse-engineered from created_at later, which would
                                   # need every order's timestamp offset to stay under one
                                   # bar-width forever (fragile at large order counts).
    order_id = 0

    def _append(sig, step: int) -> None:
        nonlocal order_id
        order_id += 1
        raw_price = _resolve_price(strategy, path, sig.symbol, step)
        fee_mult = (1.0 + fee_bps / 10_000.0) if sig.side == "buy" else (1.0 - fee_bps / 10_000.0)
        fill_price = raw_price * fee_mult
        orders.append(Order(
            id=order_id, user_id=0, mode=Mode.paper, strategy_key=strategy.key,
            symbol=sig.symbol, side=Side(sig.side), order_type=OrderType.market,
            qty=sig.qty, px=fill_price, status=OrderStatus.filled,
            filled_qty=sig.qty, avg_fill_px=fill_price,
            # One bar per minute, orders a few microseconds apart within
            # that -- purely to give _walk_fills a stable chronological
            # sort key. minutes(step) dominates the microsecond term by
            # 6 orders of magnitude, so this stays monotonic regardless
            # of how large order_id grows; the actual time unit itself
            # is meaningless here.
            created_at=_EPOCH + timedelta(minutes=step, microseconds=order_id),
        ))
        order_steps.append(step)

    for step in range(path.steps):
        for sig in strategy.evaluate(path, step):
            _append(sig, step)

    # A strategy still holding a position at the very LAST bar (e.g. an
    # options strategy whose hold_bars hasn't elapsed by path end) is
    # force-closed here, once, so compute_realizations/trade_count reflect
    # a COMPLETE picture rather than a position that's open forever with
    # nothing ever realized. Opt-in via getattr -- only
    # OptionsBacktestAdapter implements this; the other 7 adapter shapes
    # have no such method, so this is a no-op for them (byte-for-byte
    # unchanged behavior). This does NOT change the equity curve (already
    # marked every bar) or fix a degenerate Sharpe -- see
    # MAX_PLAUSIBLE_ANNUALIZED_RATIO for that; it only completes the
    # trade-level accounting.
    force_close = getattr(strategy, "force_close", None)
    if force_close is not None and path.steps > 0:
        final_step = path.steps - 1
        for sig in force_close(path, final_step):
            _append(sig, final_step)

    return orders, order_steps


def _mark_to_market_equity_curve(
    strategy: BacktestStrategy, orders: list[Order], order_steps: list[int],
    path: MultiAssetHistory, initial_cash: float,
) -> np.ndarray:
    """Bar-by-bar equity (cash + open positions valued at that bar's
    close), NOT the fill-indexed, closed-book EquityPoint series
    accounting.py's own live dashboard uses. That series only moves on a
    REALIZED fill (by design -- see EquityPoint's docstring: it's
    reconstructible from Order history with no historical price snapshots
    needed, which is the right tradeoff for a live account page). A
    backtest has the full price path already in memory, so a real
    mark-to-market curve is both possible and necessary here: a strategy
    that opens one position and holds it the entire run would otherwise
    show a perfectly flat equity curve regardless of how that position's
    paper value actually moved, making Sharpe/drawdown meaningless.

    Recomputes cash/qty/avg-entry-price via _walk_fills each time a NEW
    fill occurs (not every bar) and forward-fills between fills, since
    positions only change on a fill -- O(F^2) in the fill count F, not
    O(N) in bar count, and F is normally far smaller than N since these
    strategies trade occasionally, not every bar. This calls _walk_fills
    itself, unmodified, rather than re-deriving the weighted-average-cost
    formula a second time in this module.
    """
    n = path.steps
    equity = np.full(n, initial_cash, dtype=float)
    if not orders:
        return equity

    cash = initial_cash
    qty: dict[str, int] = {}

    fill_idx = 0
    for step in range(n):
        while fill_idx < len(orders) and order_steps[fill_idx] == step:
            fill_idx += 1
            w = _walk_fills(orders[:fill_idx], initial_cash)
            cash, qty = w.cash, dict(w.qty)
        marked = cash
        for sym, q in qty.items():
            if q == 0:
                continue
            mark = _resolve_price(strategy, path, sym, step)
            marked += q * mark
        equity[step] = marked
    return equity


def _sharpe_ratio(equity_curve: np.ndarray) -> tuple[float | None, str | None]:
    """Returns (value, invalid_reason) -- exactly one of the two is None.
    A caller that gets None back must NOT fabricate a substitute number
    (0.0 included): "no valid measurement" and "measured, and it's zero"
    are different claims, the same distinction app.dashboard_stats.
    TradeStats' own None fields already draw for win_rate/profit_factor.
    An earlier version of this function collapsed BOTH "no variance at
    all" and "no bars to measure" into a fabricated 0.0 -- which reads in
    a strategy table as "measured, and it's flat," not as "the measurement
    is invalid," and nobody questions a plausible-looking 0.0. See
    MAX_PLAUSIBLE_ANNUALIZED_RATIO's own comment for the second, more
    consequential half of this: even a NONZERO std can still produce an
    annualized ratio no real strategy could have.
    """
    if len(equity_curve) < 2:
        return None, "fewer than 2 bars"
    returns = np.diff(equity_curve) / equity_curve[:-1]
    std = returns.std(ddof=0)
    if std == 0:
        return None, "zero return variance (flat equity curve) -- no real signal to measure"
    sharpe = float(returns.mean() / std * np.sqrt(BARS_PER_YEAR))
    if abs(sharpe) > MAX_PLAUSIBLE_ANNUALIZED_RATIO:
        return None, (
            f"annualized Sharpe {sharpe:.1f} exceeds the plausible bound "
            f"{MAX_PLAUSIBLE_ANNUALIZED_RATIO:.1f} -- a near-deterministic return "
            f"series, not genuine risk-adjusted skill (raw per-bar mean/std = "
            f"{sharpe / np.sqrt(BARS_PER_YEAR):.1f})"
        )
    return sharpe, None


def _max_drawdown(equity_curve: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity_curve)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown = np.where(peak > 0, (peak - equity_curve) / peak, 0.0)
    return float(drawdown.max())


def _calmar_ratio(equity_curve: np.ndarray, max_drawdown: float) -> tuple[float | None, str | None]:
    """Same (value, invalid_reason) contract as _sharpe_ratio, and the
    same reasoning: a fabricated 0.0 substitute for "undefined" or
    "implausible" reads as a real, flat measurement, not as a warning."""
    if len(equity_curve) < 2:
        return None, "fewer than 2 bars"
    returns = np.diff(equity_curve) / equity_curve[:-1]
    annualized_return = float(returns.mean() * BARS_PER_YEAR)
    if max_drawdown <= 0:
        # No drawdown observed at all -- the ratio is undefined (division
        # by zero), not infinite, and not a real 0.0 either -- an
        # undefined ratio doesn't mean "zero risk-adjusted return."
        return None, "zero drawdown observed -- ratio undefined"
    calmar = annualized_return / max_drawdown
    if abs(calmar) > MAX_PLAUSIBLE_ANNUALIZED_RATIO:
        return None, (
            f"Calmar {calmar:.1f} exceeds the plausible bound {MAX_PLAUSIBLE_ANNUALIZED_RATIO:.1f} "
            f"-- annualized return over a near-zero drawdown, not a real risk-adjusted result"
        )
    return calmar, None


def run_backtest(
    strategy: BacktestStrategy, path: MultiAssetHistory,
    initial_cash: float = 100_000.0, fee_bps: float = 2.0,
) -> BacktestRunResult:
    orders, order_steps = _build_orders(strategy, path, fee_bps)
    equity_curve = _mark_to_market_equity_curve(strategy, orders, order_steps, path, initial_cash)

    realizations = compute_realizations(orders, starting_cash=initial_cash)
    trade_stats = compute_trade_stats(realizations)

    max_dd = _max_drawdown(equity_curve)
    sharpe, sharpe_reason = _sharpe_ratio(equity_curve)
    calmar, calmar_reason = _calmar_ratio(equity_curve, max_dd)

    return BacktestRunResult(
        strategy_key=strategy.key,
        seed=path.seed,
        steps=path.steps,
        sharpe_ratio=sharpe,
        sharpe_invalid_reason=sharpe_reason,
        win_rate=trade_stats.win_rate,
        profit_factor=trade_stats.profit_factor,
        max_drawdown=max_dd,
        calmar_ratio=calmar,
        calmar_invalid_reason=calmar_reason,
        orders_submitted=len(orders),
        round_trips_closed=trade_stats.n_trades,
        equity_curve=equity_curve,
        initial_cash=initial_cash,
        final_equity=float(equity_curve[-1]),
    )
