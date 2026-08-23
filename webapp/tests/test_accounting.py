from datetime import datetime, timedelta, timezone

import pytest

from app.accounting import compute_account
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _order(symbol, side, filled_qty, avg_fill_px, minutes_after_t0=0, status=OrderStatus.filled):
    return Order(
        user_id=1, mode=Mode.paper, symbol=symbol, side=side, order_type=OrderType.market,
        qty=filled_qty, px=None, status=status, filled_qty=filled_qty, avg_fill_px=avg_fill_px,
        created_at=T0 + timedelta(minutes=minutes_after_t0),
    )


def test_no_orders_returns_only_starting_cash():
    acc = compute_account([], current_prices={}, starting_cash=100_000.0)
    assert acc.cash == 100_000.0
    assert acc.positions == {}
    assert acc.total_value == 100_000.0


def test_single_buy_opens_a_long_position_and_moves_cash():
    orders = [_order("RELIANCE", Side.buy, 10, 2900.0)]
    acc = compute_account(orders, current_prices={"RELIANCE": 2900.0}, starting_cash=100_000.0)
    assert acc.cash == pytest.approx(100_000.0 - 10 * 2900.0)
    pos = acc.positions["RELIANCE"]
    assert pos.qty == 10
    assert pos.avg_entry_px == pytest.approx(2900.0)
    assert pos.unrealized_pnl == pytest.approx(0.0)


def test_unrealized_pnl_reflects_the_current_mark_price():
    orders = [_order("RELIANCE", Side.buy, 10, 2900.0)]
    acc = compute_account(orders, current_prices={"RELIANCE": 3000.0}, starting_cash=100_000.0)
    pos = acc.positions["RELIANCE"]
    assert pos.unrealized_pnl == pytest.approx(10 * (3000.0 - 2900.0))


def test_adding_to_a_position_blends_the_average_entry_price():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),
        _order("TCS", Side.buy, 10, 4200.0, minutes_after_t0=1),
    ]
    acc = compute_account(orders, current_prices={"TCS": 4200.0}, starting_cash=1_000_000.0)
    pos = acc.positions["TCS"]
    assert pos.qty == 20
    # (10*4000 + 10*4200) / 20 = 4100
    assert pos.avg_entry_px == pytest.approx(4100.0)


def test_partial_close_realizes_pnl_against_the_average_entry_price():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),
        _order("TCS", Side.sell, 4, 4300.0, minutes_after_t0=1),
    ]
    acc = compute_account(orders, current_prices={"TCS": 4300.0}, starting_cash=1_000_000.0)
    pos = acc.positions["TCS"]
    assert pos.qty == 6
    assert pos.avg_entry_px == pytest.approx(4000.0), "closing a PART of a position must not change the remaining average entry price"
    assert pos.realized_pnl == pytest.approx(4 * (4300.0 - 4000.0))
    assert acc.total_realized_pnl == pytest.approx(pos.realized_pnl)


def test_full_close_flattens_the_position_and_it_disappears_from_the_snapshot():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),
        _order("TCS", Side.sell, 10, 4100.0, minutes_after_t0=1),
    ]
    acc = compute_account(orders, current_prices={"TCS": 4100.0}, starting_cash=1_000_000.0)
    assert "TCS" not in acc.positions
    assert acc.total_realized_pnl == pytest.approx(10 * (4100.0 - 4000.0))


def test_flipping_through_flat_realizes_against_the_old_side_and_opens_the_new_one():
    orders = [
        _order("SBIN", Side.buy, 10, 800.0, minutes_after_t0=0),
        _order("SBIN", Side.sell, 15, 820.0, minutes_after_t0=1),  # closes the 10 long, opens 5 short
    ]
    acc = compute_account(orders, current_prices={"SBIN": 820.0}, starting_cash=1_000_000.0)
    pos = acc.positions["SBIN"]
    assert pos.qty == -5
    assert pos.avg_entry_px == pytest.approx(820.0), "the excess beyond closing the old position opens a fresh one at this fill's price"
    assert acc.total_realized_pnl == pytest.approx(10 * (820.0 - 800.0))
    assert pos.unrealized_pnl == pytest.approx(0.0)  # marked at the same price it opened at


def test_pending_and_cancelled_orders_are_ignored():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, status=OrderStatus.pending_confirmation),
        _order("TCS", Side.buy, 5, 4000.0, status=OrderStatus.cancelled),
        _order("TCS", Side.buy, 5, 4000.0, status=OrderStatus.rejected),
    ]
    acc = compute_account(orders, current_prices={"TCS": 4000.0}, starting_cash=100_000.0)
    assert acc.positions == {}
    assert acc.cash == 100_000.0


def test_multiple_symbols_are_tracked_independently():
    orders = [
        _order("RELIANCE", Side.buy, 10, 2900.0),
        _order("TCS", Side.sell, 5, 4000.0),
    ]
    acc = compute_account(orders, current_prices={"RELIANCE": 2900.0, "TCS": 4000.0}, starting_cash=1_000_000.0)
    assert acc.positions["RELIANCE"].qty == 10
    assert acc.positions["TCS"].qty == -5


def test_processes_fills_in_chronological_order_regardless_of_input_order():
    """A sell-then-buy in wall-clock time must not be processed as
    buy-then-sell just because that's the order it appears in the list --
    average cost accounting is order-dependent, so this has to be right."""
    orders = [
        _order("TCS", Side.sell, 10, 4300.0, minutes_after_t0=1),  # appears first in the list...
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),   # ...but happened first in time
    ]
    acc = compute_account(orders, current_prices={"TCS": 4300.0}, starting_cash=1_000_000.0)
    assert "TCS" not in acc.positions
    assert acc.total_realized_pnl == pytest.approx(10 * (4300.0 - 4000.0))
