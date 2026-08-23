"""Win rate, profit factor, avg win/loss, and the daily P&L calendar --
all computed from app.accounting.compute_realizations, the same
individual close/reduce/flip events accounting.py already derives from
Order history. No separately-tracked "trade outcome" table.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.accounting import TradeRealization, compute_realizations
from app.models.trading import Order


@dataclass(frozen=True)
class TradeStats:
    net_pnl: float
    win_rate: float | None          # None, not 0.0, when there are no trades yet -- "no data" and "0% win rate" are different claims
    profit_factor: float | None     # None when there are no losing trades to divide by (avoids a fake infinity)
    avg_win: float | None
    avg_loss: float | None          # positive magnitude
    n_trades: int


@dataclass(frozen=True)
class DayStats:
    day: date
    pnl: float
    n_trades: int


def compute_trade_stats(realizations: list[TradeRealization]) -> TradeStats:
    if not realizations:
        return TradeStats(net_pnl=0.0, win_rate=None, profit_factor=None, avg_win=None, avg_loss=None, n_trades=0)

    wins = [r.amount for r in realizations if r.amount > 0]
    losses = [-r.amount for r in realizations if r.amount < 0]  # positive magnitudes
    net = sum(r.amount for r in realizations)

    gross_win = sum(wins)
    gross_loss = sum(losses)

    return TradeStats(
        net_pnl=net,
        win_rate=len(wins) / len(realizations),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        avg_win=(gross_win / len(wins)) if wins else None,
        avg_loss=(gross_loss / len(losses)) if losses else None,
        n_trades=len(realizations),
    )


def compute_day_stats(realizations: list[TradeRealization]) -> list[DayStats]:
    """One row per calendar day that had at least one realized trade --
    the P&L calendar heatmap. Uses the realization's own created_at (the
    CLOSING fill's timestamp), matching how a real trading-journal
    calendar attributes a trade to the day it was closed, not opened."""
    by_day: dict[date, list[float]] = defaultdict(list)
    for r in realizations:
        by_day[r.created_at.date()].append(r.amount)

    return sorted(
        (DayStats(day=d, pnl=sum(amounts), n_trades=len(amounts)) for d, amounts in by_day.items()),
        key=lambda s: s.day,
    )


def compute_day_win_rate(day_stats: list[DayStats]) -> float | None:
    if not day_stats:
        return None
    winning_days = sum(1 for d in day_stats if d.pnl > 0)
    return winning_days / len(day_stats)
