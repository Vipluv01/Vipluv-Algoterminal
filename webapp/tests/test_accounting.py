from datetime import datetime, timedelta, timezone

import pytest

from app.accounting import compute_account, compute_equity_curve, compute_realized_pnl_curve, compute_realizations
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


def test_compute_realizations_is_empty_when_nothing_has_closed():
    orders = [_order("RELIANCE", Side.buy, 10, 2900.0)]
    assert compute_realizations(orders) == []


def test_compute_realizations_records_one_event_per_closing_fill():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),
        _order("TCS", Side.sell, 4, 4300.0, minutes_after_t0=1),   # win
        _order("TCS", Side.buy, 4, 4000.0, minutes_after_t0=2),    # re-adds, no realization
        _order("TCS", Side.sell, 4, 3900.0, minutes_after_t0=3),   # loss
    ]
    events = compute_realizations(orders)
    assert len(events) == 2
    assert events[0].amount == pytest.approx(4 * (4300.0 - 4000.0))
    assert events[1].amount == pytest.approx(4 * (3900.0 - 4000.0))
    assert all(e.symbol == "TCS" for e in events)


def test_compute_realized_pnl_curve_is_empty_with_no_orders():
    assert compute_realized_pnl_curve([]) == []


def test_compute_realized_pnl_curve_has_one_point_per_fill_including_non_realizing_ones():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),   # opens -- no realization yet
        _order("TCS", Side.sell, 4, 4300.0, minutes_after_t0=1),   # realizes a win
    ]
    curve = compute_realized_pnl_curve(orders, starting_cash=100_000.0)
    assert len(curve) == 2, "a point per FILL, not just per realizing fill -- the curve must show flat stretches while a position is only being built, not skip them"
    assert curve[0].realized_pnl == pytest.approx(100_000.0), "opening a position doesn't change realized P&L yet"
    assert curve[1].realized_pnl == pytest.approx(100_000.0 + 4 * (4300.0 - 4000.0))


def test_compute_realized_pnl_curve_is_chronological_regardless_of_input_order():
    orders = [
        _order("TCS", Side.sell, 10, 4300.0, minutes_after_t0=1),
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),
    ]
    curve = compute_realized_pnl_curve(orders, starting_cash=100_000.0)
    assert curve[0].created_at < curve[1].created_at
    assert curve[-1].realized_pnl == pytest.approx(100_000.0 + 10 * (4300.0 - 4000.0))


def test_compute_realized_pnl_curve_matches_total_realized_pnl_at_the_final_point():
    """The whole point of this curve: its LAST value must equal
    starting_cash + total realized P&L, exactly what compute_account
    reports separately -- two views of the same walk that must agree,
    same discipline as the existing compute_realizations cross-check."""
    orders = [
        _order("RELIANCE", Side.buy, 10, 2900.0, minutes_after_t0=0),
        _order("RELIANCE", Side.sell, 6, 3000.0, minutes_after_t0=1),
        _order("TCS", Side.sell, 5, 4000.0, minutes_after_t0=2),
        _order("TCS", Side.buy, 5, 3900.0, minutes_after_t0=3),
    ]
    acc = compute_account(orders, current_prices={"RELIANCE": 3000.0, "TCS": 3900.0}, starting_cash=1_000_000.0)
    curve = compute_realized_pnl_curve(orders, starting_cash=1_000_000.0)
    assert curve[-1].realized_pnl == pytest.approx(1_000_000.0 + acc.total_realized_pnl)


def test_compute_realized_pnl_curve_ignores_pending_and_cancelled_orders():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, status=OrderStatus.pending_confirmation),
        _order("TCS", Side.buy, 5, 4000.0, status=OrderStatus.cancelled),
    ]
    assert compute_realized_pnl_curve(orders, starting_cash=100_000.0) == []


# --- compute_equity_curve: genuine mark-to-market, using an injected
# PriceLookup (not a real MarketRegistry -- app/routers/account.py wires
# the real one from SymbolMarket.price_history; these tests only need to
# prove the WALK computes mark-to-market correctly from whatever a lookup
# returns).

def _fixed_price(prices: dict[str, float]):
    """A PriceLookup that ignores the timestamp and always returns each
    symbol's current price -- for tests where "the price never moved"
    is exactly the scenario, so the equity curve's per-fill mark should
    equal compute_account's own total_value once all orders are in."""
    def lookup(symbol, at):
        return prices.get(symbol)
    return lookup


def test_compute_equity_curve_is_empty_with_no_orders():
    assert compute_equity_curve([], price_lookup=_fixed_price({})) == []


def test_compute_equity_curve_marks_an_open_position_at_the_looked_up_price_not_realized_pnl():
    """The bug this whole fix is for: an OPEN position must move the
    curve by its unrealized P&L, not sit flat at starting_cash the way
    the realized-only curve does."""
    orders = [_order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0)]
    curve = compute_equity_curve(orders, price_lookup=_fixed_price({"TCS": 4300.0}), starting_cash=100_000.0)
    assert len(curve) == 1
    # cash after the buy (100_000 - 40_000) + 10 * mark (4300) == 103_000,
    # NOT 100_000 (what the realized-only curve would show for this
    # still-open position).
    assert curve[0].equity == pytest.approx(100_000.0 - 10 * 4000.0 + 10 * 4300.0)


def test_compute_equity_curve_moves_with_the_looked_up_price_between_fills():
    """Same order, two different marks at two different timestamps
    (simulating price_history moving between fills) -- the curve must
    track each fill's own mark, not freeze at the first one."""
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),
        _order("TCS", Side.buy, 10, 4000.0, minutes_after_t0=1),
    ]

    def lookup(symbol, at):
        # Distinguish the two fills by their own created_at.
        return 4100.0 if at == T0 else 4400.0

    curve = compute_equity_curve(orders, price_lookup=lookup, starting_cash=100_000.0)
    assert len(curve) == 2
    assert curve[0].equity == pytest.approx(100_000.0 - 10 * 4000.0 + 10 * 4100.0)
    assert curve[1].equity == pytest.approx(100_000.0 - 20 * 4000.0 + 20 * 4400.0)
    assert curve[0].equity != curve[1].equity, "a real price move between fills must move the curve"


def test_compute_equity_curve_falls_back_to_avg_entry_price_when_the_lookup_has_no_history():
    """A symbol the lookup can't mark (e.g. a synthetic option contract
    with no price_history) falls back to that position's own average
    entry price -- the same honest fallback compute_account already uses
    for current_prices, not a fabricated mark."""
    orders = [_order("XYZ_OPT", Side.buy, 10, 50.0, minutes_after_t0=0)]
    curve = compute_equity_curve(orders, price_lookup=_fixed_price({}), starting_cash=100_000.0)
    assert curve[0].equity == pytest.approx(100_000.0), "marked at its own avg entry price -- no gain, no loss"


def test_compute_equity_curve_matches_total_value_once_flat_and_the_price_is_static():
    """At the final point of a fully-closed round trip, mark-to-market
    and realized-only must agree exactly -- there's no open position left
    for mark-to-market to disagree about."""
    orders = [
        _order("RELIANCE", Side.buy, 10, 2900.0, minutes_after_t0=0),
        _order("RELIANCE", Side.sell, 10, 3000.0, minutes_after_t0=1),
    ]
    acc = compute_account(orders, current_prices={"RELIANCE": 3000.0}, starting_cash=1_000_000.0)
    curve = compute_equity_curve(orders, price_lookup=_fixed_price({"RELIANCE": 3000.0}), starting_cash=1_000_000.0)
    assert curve[-1].equity == pytest.approx(acc.total_value)


def test_compute_equity_curve_matches_total_value_with_an_open_position_too():
    """Not just the flat/closed case: at the LATEST point, mark-to-market
    equity must equal compute_account's total_value even with a position
    still open -- this is the exact disagreement the bug report described
    (100,000 vs 100,500 at the identical instant)."""
    orders = [_order("RELIANCE", Side.buy, 10, 2900.0, minutes_after_t0=0)]
    acc = compute_account(orders, current_prices={"RELIANCE": 2950.0}, starting_cash=100_000.0)
    curve = compute_equity_curve(orders, price_lookup=_fixed_price({"RELIANCE": 2950.0}), starting_cash=100_000.0)
    assert curve[-1].equity == pytest.approx(acc.total_value)
    assert curve[-1].equity != pytest.approx(100_000.0), "an open position must move the curve away from starting cash"


def test_compute_equity_curve_still_realizes_correctly_through_a_flip_through_flat():
    """Same flip-through-flat scenario as
    test_flipping_through_flat_realizes_against_the_old_side_and_opens_the_new_one
    above -- the mark-to-market path must reuse the same weighted-avg-cost
    walk, not a second, divergent implementation."""
    orders = [
        _order("SBIN", Side.buy, 10, 800.0, minutes_after_t0=0),
        _order("SBIN", Side.sell, 15, 820.0, minutes_after_t0=1),  # closes the 10 long, opens 5 short
    ]
    curve = compute_equity_curve(orders, price_lookup=_fixed_price({"SBIN": 820.0}), starting_cash=1_000_000.0)
    # After the flip: cash moved by (-10*800 + 15*820) = 4300; the
    # resulting 5-short position is marked at 820, the same price it
    # opened at, so it contributes 0 unrealized on top of that.
    assert curve[-1].equity == pytest.approx(1_000_000.0 - 10 * 800.0 + 15 * 820.0 + (-5) * 820.0)
    assert curve[-1].equity == pytest.approx(1_000_000.0 + 10 * (820.0 - 800.0))


def test_compute_equity_curve_ignores_pending_and_cancelled_orders():
    orders = [
        _order("TCS", Side.buy, 10, 4000.0, status=OrderStatus.pending_confirmation),
        _order("TCS", Side.buy, 5, 4000.0, status=OrderStatus.cancelled),
    ]
    assert compute_equity_curve(orders, price_lookup=_fixed_price({}), starting_cash=100_000.0) == []


def test_compute_realizations_matches_compute_accounts_total(monkeypatch):
    """The two views must agree -- they now share the same underlying walk,
    so this is really a guard against that refactor ever drifting."""
    orders = [
        _order("RELIANCE", Side.buy, 10, 2900.0, minutes_after_t0=0),
        _order("RELIANCE", Side.sell, 6, 3000.0, minutes_after_t0=1),
        _order("TCS", Side.sell, 5, 4000.0, minutes_after_t0=2),
        _order("TCS", Side.buy, 5, 3900.0, minutes_after_t0=3),
    ]
    acc = compute_account(orders, current_prices={"RELIANCE": 3000.0, "TCS": 3900.0}, starting_cash=1_000_000.0)
    events = compute_realizations(orders, starting_cash=1_000_000.0)
    assert sum(e.amount for e in events) == pytest.approx(acc.total_realized_pnl)
