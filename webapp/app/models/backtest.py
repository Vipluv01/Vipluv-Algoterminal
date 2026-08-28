"""Persisted Monte Carlo backtest results -- one row per (strategy_key,
n_paths, n_bars, seed) sweep, written by scripts/run_backtests.py.

This table exists to replace every hand-typed strategy performance number
in the app with something a screen can point at and say "computed by this
run, on this date, over this many paths" -- see app/backtest/monte_carlo.py's
module docstring. A strategy with no row here has no number to show, full
stop; there is deliberately no default/fallback value anywhere downstream.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StrategyBacktest(Base):
    __tablename__ = "strategy_backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_key: Mapped[str] = mapped_column(String(64), index=True)
    n_paths: Mapped[int] = mapped_column(Integer)
    n_bars: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)

    # sharpe_median/ci_low/ci_high and calmar_ratio are nullable for the
    # same reason win_rate/profit_factor already are: None ("no valid
    # measurement") and a real number ("measured, and it's this") are
    # different claims. A near-deterministic return series (e.g. a
    # theta-dominated options position with no completed round-trip) can
    # produce an annualized ratio no real strategy could have --
    # app.backtest.engine.MAX_PLAUSIBLE_ANNUALIZED_RATIO catches that and
    # reports None rather than a number that looks plausible but isn't.
    sharpe_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe_ci_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe_ci_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    # How many of n_paths actually produced the sharpe_median above (see
    # app.backtest.engine.MAX_PLAUSIBLE_ANNUALIZED_RATIO) -- without this,
    # a median drawn from 2 surviving paths out of 5 reads identically to
    # one drawn from all 5, and the reader has no way to tell a thin
    # sample from a solid one. n_paths itself is the total attempted.
    n_valid_paths: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Two DELIBERATELY separate counts, not one "total_trades" -- a
    # strategy holding one open position the entire path submits real
    # orders but realizes zero ROUND TRIPS; collapsing that into a single
    # number would make real order flow (and real equity movement) look
    # like "0 trades." Never render a performance ratio against zero in
    # either without checking first.
    orders_submitted: Mapped[int] = mapped_column(Integer)
    round_trips_closed: Mapped[int] = mapped_column(Integer)

    generated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
