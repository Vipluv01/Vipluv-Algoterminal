from datetime import datetime, timezone

import pytest

from app.accounting import TradeRealization
from app.dashboard_stats import compute_day_stats, compute_day_win_rate, compute_trade_stats


def _r(amount, minutes_after=0, day_offset=0):
    return TradeRealization(
        order_id=1, symbol="TCS", strategy_key=None, amount=amount,
        created_at=datetime(2026, 1, 1 + day_offset, tzinfo=timezone.utc),
    )


def test_no_trades_returns_none_stats_not_zero():
    stats = compute_trade_stats([])
    assert stats.n_trades == 0
    assert stats.win_rate is None
    assert stats.profit_factor is None
    assert stats.avg_win is None
    assert stats.avg_loss is None
    assert stats.net_pnl == 0.0


def test_all_wins_gives_100pct_win_rate_and_no_profit_factor():
    """Profit factor is gross win / gross loss -- with zero losses, that's
    a division by zero, and reporting it as None (not a fake infinity) is
    the honest answer, not a display bug to paper over."""
    stats = compute_trade_stats([_r(100.0), _r(50.0)])
    assert stats.win_rate == 1.0
    assert stats.profit_factor is None
    assert stats.avg_win == pytest.approx(75.0)
    assert stats.avg_loss is None


def test_mixed_wins_and_losses():
    stats = compute_trade_stats([_r(100.0), _r(-40.0), _r(60.0), _r(-20.0)])
    assert stats.n_trades == 4
    assert stats.win_rate == pytest.approx(0.5)
    assert stats.net_pnl == pytest.approx(100.0)
    assert stats.avg_win == pytest.approx(80.0)   # (100+60)/2
    assert stats.avg_loss == pytest.approx(30.0)  # (40+20)/2
    assert stats.profit_factor == pytest.approx(160.0 / 60.0)


def test_a_realization_of_exactly_zero_counts_as_neither_win_nor_loss():
    stats = compute_trade_stats([_r(0.0), _r(50.0)])
    assert stats.n_trades == 2
    assert stats.win_rate == pytest.approx(0.5)  # 1 win out of 2 trades
    assert stats.avg_win == pytest.approx(50.0)
    assert stats.avg_loss is None


def test_day_stats_groups_by_calendar_day_of_the_closing_fill():
    events = [_r(100.0, day_offset=0), _r(-30.0, day_offset=0), _r(50.0, day_offset=1)]
    days = compute_day_stats(events)
    assert len(days) == 2
    assert days[0].pnl == pytest.approx(70.0)
    assert days[0].n_trades == 2
    assert days[1].pnl == pytest.approx(50.0)
    assert days[1].n_trades == 1


def test_day_stats_are_sorted_chronologically():
    events = [_r(1.0, day_offset=2), _r(1.0, day_offset=0), _r(1.0, day_offset=1)]
    days = compute_day_stats(events)
    assert [d.day.day for d in days] == [1, 2, 3]


def test_day_win_rate_counts_only_net_positive_days():
    events = [_r(100.0, day_offset=0), _r(-30.0, day_offset=0),  # day 0: net +70, a winning day
              _r(-10.0, day_offset=1)]                            # day 1: net -10, a losing day
    days = compute_day_stats(events)
    assert compute_day_win_rate(days) == pytest.approx(0.5)


def test_day_win_rate_is_none_with_no_days():
    assert compute_day_win_rate([]) is None
