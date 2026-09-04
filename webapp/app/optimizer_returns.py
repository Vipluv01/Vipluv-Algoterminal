"""Builds per-strategy DAILY return series from real realized-P&L events --
the raw material max_sharpe_allocation (app/portfolio_optimizer.py) needs,
which nothing else in the codebase already produces: app/dashboard_stats.py
groups realizations by day OR by strategy, never both together, and never
as an aligned, zero-filled time series multiple strategies can be directly
compared against (confirmed by search before writing this, not assumed).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import numpy as np

from app.accounting import STARTING_PAPER_CASH_DEFAULT, TradeRealization

# Below this many distinct trading days across the active strategies, a
# Sharpe estimate is noise, not a signal -- max_sharpe_allocation itself
# has no opinion on this (it'll happily "optimize" 2 days of data), so the
# floor belongs here, at the point where real trade history becomes a
# return series, not inside the optimizer's own general-purpose math.
MIN_TRADING_DAYS = 5


def build_daily_return_series(
    realizations: list[TradeRealization], strategy_keys: list[str],
    *, starting_cash: float = STARTING_PAPER_CASH_DEFAULT,
) -> dict[str, np.ndarray] | None:
    """Returns {strategy_key: daily_return_array}, every array aligned to
    the same sorted calendar dates and zero-filled on days a strategy had
    no realized P&L -- or None if there isn't enough real history yet
    (fewer than 2 strategies with any realized trade at all, or fewer than
    MIN_TRADING_DAYS distinct days across them) for a Sharpe estimate to
    mean anything.

    Takes realizations directly (not raw orders + starting_cash to walk
    itself) so a caller with an already-computed list -- GET /optimizer
    uses app.accounting.get_cached_realizations, the same incremental
    cache GET /dashboard/stats uses, rather than re-walking this user's
    entire order history from scratch on every request -- doesn't pay for
    a second, redundant walk here. starting_cash is still needed on its
    own: it's the denominator each day's raw rupee P&L is normalized by
    to get a return, a separate concern from how the realizations
    themselves were produced.
    """
    by_strategy_day: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for r in realizations:
        if r.strategy_key not in strategy_keys:
            continue
        by_strategy_day[r.strategy_key][r.created_at.date()] += r.amount

    active = [k for k in strategy_keys if by_strategy_day.get(k)]
    if len(active) < 2:
        return None

    all_days = sorted({d for k in active for d in by_strategy_day[k]})
    if len(all_days) < MIN_TRADING_DAYS:
        return None

    return {
        k: np.array([by_strategy_day[k].get(d, 0.0) / starting_cash for d in all_days])
        for k in active
    }
