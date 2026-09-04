from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounting import (
    STARTING_PAPER_CASH_DEFAULT,
    STARTING_VIRTUAL_CASH_DEFAULT,
    _account_cache,
    _realizations_cache,
    _WalkState,
    compute_account,
    compute_equity_curve,
    compute_realized_pnl_curve,
    compute_realizations,
    get_cached_account_snapshot,
    get_cached_realizations,
)
from app.db import Base
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User

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


# ---------------------------------------------------------------------------
# get_cached_account_snapshot: the incremental cache added 2026-09-04 to fix
# a real, confirmed-live latency problem (GET /account re-walking a 107,651-
# order paper account from scratch on every poll). Unlike every test above,
# this one needs a real DB session -- get_cached_account_snapshot queries
# Order.id directly (the incremental watermark), which only exists once a
# row has actually been inserted.
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def user(db):
    u = User(google_sub="s", email="e@x.com", display_name="T")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture(autouse=True)
def _clear_account_cache():
    """_account_cache is process-global by design (get_cached_account_
    snapshot's own docstring explains why -- a small, single-process dev
    deployment, same scope as app/broker/adapter_cache.py's own cache).
    Each test below gets a FRESH in-memory DB whose first user is very
    likely to reuse id=1 -- without clearing this between tests, a later
    test could read an earlier test's cached state for the same
    (user_id, mode, sub_account_id, only_primary) key and silently pass
    for the wrong reason. _realizations_cache is the same kind of cache
    (see get_cached_realizations) and needs the same treatment."""
    _account_cache.clear()
    _realizations_cache.clear()
    yield
    _account_cache.clear()
    _realizations_cache.clear()


def _db_order(user_id, symbol, side, filled_qty, avg_fill_px, minutes_after_t0=0, mode=Mode.paper, sub_account_id=None):
    return Order(
        user_id=user_id, mode=mode, symbol=symbol, side=side, order_type=OrderType.market,
        qty=filled_qty, px=None, status=OrderStatus.filled, filled_qty=filled_qty, avg_fill_px=avg_fill_px,
        created_at=T0 + timedelta(minutes=minutes_after_t0), sub_account_id=sub_account_id,
    )


def test_cached_snapshot_matches_a_full_walk_across_incremental_batches(db, user):
    """The single most important test for this cache: simulate real usage
    (new fills arriving between polls, e.g. AccountPanel.js hitting GET
    /account every few seconds) by inserting orders in three separate
    batches and calling get_cached_account_snapshot after each one --
    then confirm the FINAL cached snapshot is identical to calling
    compute_account once on the full order history fetched fresh. Covers
    opens, adds, partial closes, and flips-through-flat in BOTH directions
    across two symbols, since a bug in the incremental path could easily
    hide behind a scenario that never actually flips or partially closes
    anything."""
    prices = {"RELIANCE": 3180.0, "TCS": 4050.0}

    batch1 = [
        _db_order(user.id, "RELIANCE", Side.buy, 10, 2900.0, minutes_after_t0=0),   # open long 10
        _db_order(user.id, "RELIANCE", Side.buy, 5, 3000.0, minutes_after_t0=1),    # add -> long 15
        _db_order(user.id, "TCS", Side.sell, 8, 4000.0, minutes_after_t0=2),        # open short 8
    ]
    db.add_all(batch1)
    db.commit()
    snap1 = get_cached_account_snapshot(db, user.id, Mode.paper, prices)
    assert snap1.positions["RELIANCE"].qty == 15
    assert snap1.positions["TCS"].qty == -8

    batch2 = [
        _db_order(user.id, "RELIANCE", Side.sell, 6, 3100.0, minutes_after_t0=3),   # partial close -> long 9
        _db_order(user.id, "TCS", Side.buy, 12, 3900.0, minutes_after_t0=4),        # flip short 8 -> long 4
        _db_order(user.id, "RELIANCE", Side.sell, 20, 3200.0, minutes_after_t0=5),  # flip long 9 -> short 11
    ]
    db.add_all(batch2)
    db.commit()
    snap2 = get_cached_account_snapshot(db, user.id, Mode.paper, prices)
    assert snap2.positions["RELIANCE"].qty == -11
    assert snap2.positions["TCS"].qty == 4

    batch3 = [
        _db_order(user.id, "TCS", Side.sell, 4, 4100.0, minutes_after_t0=6),        # fully closes TCS
        _db_order(user.id, "RELIANCE", Side.buy, 3, 3150.0, minutes_after_t0=7),    # partial close -> short 8
    ]
    db.add_all(batch3)
    db.commit()
    snap3 = get_cached_account_snapshot(db, user.id, Mode.paper, prices)

    # A repeat call with no new orders in between must be idempotent --
    # no double-counting a fill that was already folded into the cache.
    snap3_again = get_cached_account_snapshot(db, user.id, Mode.paper, prices)
    assert snap3_again == snap3

    all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    expected = compute_account(all_orders, current_prices=prices, starting_cash=STARTING_PAPER_CASH_DEFAULT)

    assert snap3.cash == pytest.approx(expected.cash)
    assert snap3.total_realized_pnl == pytest.approx(expected.total_realized_pnl)
    assert snap3.total_unrealized_pnl == pytest.approx(expected.total_unrealized_pnl)
    assert set(snap3.positions.keys()) == set(expected.positions.keys())
    for sym, pos in expected.positions.items():
        got = snap3.positions[sym]
        assert got.qty == pos.qty
        assert got.avg_entry_px == pytest.approx(pos.avg_entry_px)
        assert got.realized_pnl == pytest.approx(pos.realized_pnl)
        assert got.unrealized_pnl == pytest.approx(pos.unrealized_pnl)
    assert "TCS" not in snap3.positions, "TCS was fully closed in batch 3 and must not linger in the snapshot"


def test_cache_key_isolates_by_user_mode_and_sub_account(db, user):
    """The cache key is (user_id, mode, sub_account_id, only_primary) --
    confirm none of those axes leak into another: a caching bug here
    would silently show one user's or one mode's positions under
    another's, which is a much worse failure than the latency this cache
    exists to fix."""
    other_user = User(google_sub="s2", email="e2@x.com", display_name="T2")
    db.add(other_user)
    db.commit()
    db.refresh(other_user)

    orders = [
        _db_order(user.id, "RELIANCE", Side.buy, 10, 2900.0, mode=Mode.paper, minutes_after_t0=0),
        _db_order(user.id, "RELIANCE", Side.buy, 4, 2900.0, mode=Mode.virtual, minutes_after_t0=1),
        _db_order(other_user.id, "RELIANCE", Side.buy, 7, 2900.0, mode=Mode.paper, minutes_after_t0=2),
        _db_order(user.id, "TCS", Side.buy, 2, 4000.0, mode=Mode.paper, minutes_after_t0=3, sub_account_id=5),
    ]
    db.add_all(orders)
    db.commit()

    prices = {"RELIANCE": 2900.0, "TCS": 4000.0}
    user_paper = get_cached_account_snapshot(db, user.id, Mode.paper, prices)
    user_virtual = get_cached_account_snapshot(db, user.id, Mode.virtual, prices, starting_cash=STARTING_VIRTUAL_CASH_DEFAULT)
    other_paper = get_cached_account_snapshot(db, other_user.id, Mode.paper, prices)
    user_paper_only_primary = get_cached_account_snapshot(db, user.id, Mode.paper, prices, only_primary=True)
    user_paper_sub5 = get_cached_account_snapshot(db, user.id, Mode.paper, prices, sub_account_id=5)

    assert user_paper.positions["RELIANCE"].qty == 10
    assert "TCS" in user_paper.positions, "unfiltered view (neither sub_account_id nor only_primary) includes the sub-account order too"
    assert user_virtual.positions["RELIANCE"].qty == 4
    assert other_paper.positions["RELIANCE"].qty == 7
    assert "TCS" not in user_paper_only_primary.positions
    assert user_paper_sub5.positions["TCS"].qty == 2
    assert "RELIANCE" not in user_paper_sub5.positions


def test_get_cached_account_snapshot_rejects_conflicting_filters(db, user):
    with pytest.raises(ValueError):
        get_cached_account_snapshot(db, user.id, Mode.paper, {}, sub_account_id=1, only_primary=True)


def test_get_cached_account_snapshot_strategy_key_filter_isolates_from_manual_trades(db, user):
    """strategy_key, added 2026-09-04 for app/pairs_service.py's own
    _open_legs (Pair Overview/Analytics, polled every 5s -- previously an
    uncached compute_account walk on every call), scopes the walk to just
    that strategy's own tagged fills. A manual (strategy_key=None) trade
    on the same symbol must not be mistaken for the strategy's own
    position -- same isolation compute_pair_position_state's own
    "ignores orders from a different strategy" test already covers for
    pairs_service.py's separate, lighter-weight cache."""
    orders = [
        _db_order(user.id, "RELIANCE", Side.buy, 10, 2900.0, minutes_after_t0=0),
    ]
    orders[0].strategy_key = "some_strategy"
    manual = _db_order(user.id, "RELIANCE", Side.buy, 4, 2900.0, minutes_after_t0=1)
    for o in orders + [manual]:
        db.add(o)
    db.commit()

    strategy_snapshot = get_cached_account_snapshot(
        db, user.id, Mode.paper, {"RELIANCE": 2900.0}, starting_cash=0.0, strategy_key="some_strategy",
    )
    assert strategy_snapshot.positions["RELIANCE"].qty == 10, "must include only the strategy-tagged fill, not the manual one"

    unfiltered_snapshot = get_cached_account_snapshot(db, user.id, Mode.paper, {"RELIANCE": 2900.0})
    assert unfiltered_snapshot.positions["RELIANCE"].qty == 14, "the default (no strategy_key) view is unaffected -- it still sees every order"


def test_cached_snapshot_rebuilds_when_its_watermark_order_no_longer_exists(db, user):
    """Regression test for a real bug this cache's own test suite hit
    immediately: _account_cache is process-global (by design -- see its
    own docstring), but tests/conftest.py hands every test a brand new,
    empty in-memory SQLite DB. A first test populates the cache for
    (user_id=1, Mode.paper, ...); a second test's fresh DB reuses that
    same user_id=1 with its OWN unrelated orders starting again from
    Order.id=1 -- without a staleness check, the cache would keep
    serving the first test's stale positions (or worse, silently drop
    every 'new' order because their ids sit below the stale watermark).
    This constructs that exact scenario directly: prime the cache against
    one real order, then simulate the underlying DB having moved out from
    under it by forging a state whose last_order_id no longer exists."""
    order = _db_order(user.id, "RELIANCE", Side.buy, 10, 2900.0)
    db.add(order)
    db.commit()
    snap = get_cached_account_snapshot(db, user.id, Mode.paper, {"RELIANCE": 2900.0})
    assert snap.positions["RELIANCE"].qty == 10

    # Forge exactly what a stale cache from a DIFFERENT, now-gone database
    # looks like: a watermark pointing at an order id this DB has never
    # had.
    key = (user.id, Mode.paper, None, False, None)
    _account_cache[key].last_order_id = 999_999

    fresh_order = _db_order(user.id, "TCS", Side.sell, 3, 4000.0, minutes_after_t0=1)
    db.add(fresh_order)
    db.commit()

    rebuilt = get_cached_account_snapshot(db, user.id, Mode.paper, {"RELIANCE": 2900.0, "TCS": 4000.0})
    assert rebuilt.positions["RELIANCE"].qty == 10, "a stale watermark must trigger a full rebuild, not silently drop the real RELIANCE position"
    assert rebuilt.positions["TCS"].qty == -3, "and must still pick up the order that arrived after the stale watermark was forged"


def test_cached_snapshot_rebuilds_when_the_watermark_id_belongs_to_a_different_mode(db, user):
    """A stricter version of the test above, for a real bug the FIRST
    version of this staleness check had: it only confirmed 'an order with
    this id exists SOMEWHERE', not that it belongs to the same (user_id,
    mode) this cache key is for. That weak check is satisfied by
    coincidence whenever the stale watermark id numerically matches a
    REAL order that just happens to be for a different mode -- exactly
    what happens across two consecutive tests sharing one in-memory
    SQLite DB, where ids restart small each time. Confirmed live: this
    exact scenario let a virtual-mode order's data leak into a paper-mode
    read in test_portfolio_api.py's own realized-pnl-curve test before
    the check was scoped through the same user_id/mode filter the main
    query already uses."""
    virtual_order = _db_order(user.id, "RELIANCE", Side.buy, 10, 2900.0, mode=Mode.virtual)
    db.add(virtual_order)
    db.commit()

    # Forge a PAPER-mode cache entry as if a REAL earlier paper position
    # existed (a RELIANCE long), with its watermark pointing at the
    # VIRTUAL order's real id -- that id genuinely exists in this DB,
    # just not for paper. Baking in an actual position (not just a bare
    # watermark) is what makes this test able to tell the two behaviors
    # apart: an unscoped staleness check would wrongly treat this as
    # still valid and let the forged RELIANCE position leak straight into
    # the snapshot; a properly scoped one discards it and rebuilds clean.
    key = (user.id, Mode.paper, None, False, None)
    _account_cache[key] = _WalkState(
        cash=100_000.0 - 10 * 2900.0, qty={"RELIANCE": 10}, avg_px={"RELIANCE": 2900.0},
        last_order_id=virtual_order.id,
    )

    paper_order = _db_order(user.id, "TCS", Side.buy, 4, 4000.0, mode=Mode.paper, minutes_after_t0=1)
    db.add(paper_order)
    db.commit()

    snap = get_cached_account_snapshot(db, user.id, Mode.paper, {"TCS": 4000.0})
    assert "RELIANCE" not in snap.positions, "the virtual-mode order must never leak into a paper-mode snapshot"
    assert snap.positions["TCS"].qty == 4, "the real paper order must still be picked up after the stale cross-mode watermark is discarded"


# ---------------------------------------------------------------------------
# get_cached_realizations: the same incremental fix, for GET /dashboard/
# stats and GET /dashboard/calendar (2026-09-04) -- these two need the
# FULL list of realized close/reduce/flip events, not just a terminal
# snapshot, so this is a second, separate cache from _account_cache
# (see get_cached_realizations' own docstring for why it isn't folded in).
# ---------------------------------------------------------------------------

def test_cached_realizations_match_a_full_walk_across_incremental_batches(db, user):
    """Same discipline as the account-snapshot consistency test above:
    drive the cache across three batches of inserted orders and confirm
    the final cached realizations list matches compute_realizations on
    the full order history fetched fresh -- covering wins, losses, a
    re-add that must NOT realize anything, and a flip-through-flat."""
    batch1 = [
        _db_order(user.id, "TCS", Side.buy, 10, 4000.0, minutes_after_t0=0),   # opens -- no realization
        _db_order(user.id, "TCS", Side.sell, 4, 4300.0, minutes_after_t0=1),   # win
    ]
    db.add_all(batch1)
    db.commit()
    r1 = get_cached_realizations(db, user.id, Mode.paper)
    assert len(r1) == 1
    assert r1[0].amount == pytest.approx(4 * (4300.0 - 4000.0))

    batch2 = [
        _db_order(user.id, "TCS", Side.buy, 4, 4000.0, minutes_after_t0=2),    # re-adds -- no realization
        _db_order(user.id, "TCS", Side.sell, 4, 3900.0, minutes_after_t0=3),   # loss
        _db_order(user.id, "SBIN", Side.buy, 10, 800.0, minutes_after_t0=4),
        _db_order(user.id, "SBIN", Side.sell, 15, 820.0, minutes_after_t0=5),  # flip-through-flat
    ]
    db.add_all(batch2)
    db.commit()
    r2 = get_cached_realizations(db, user.id, Mode.paper)
    assert len(r2) == 3

    all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    expected = compute_realizations(all_orders)
    assert len(r2) == len(expected)
    assert sum(r.amount for r in r2) == pytest.approx(sum(e.amount for e in expected))
    assert {r.symbol for r in r2} == {e.symbol for e in expected}

    # dashboard_stats.compute_trade_stats/compute_day_stats only sum,
    # count, and day-bucket -- never assume insertion order -- so this is
    # the property that actually matters to the real consumers.
    from app.dashboard_stats import compute_day_stats, compute_trade_stats
    assert compute_trade_stats(r2) == compute_trade_stats(expected)
    assert compute_day_stats(r2) == compute_day_stats(expected)


def test_cached_realizations_is_idempotent_and_isolated_by_user_and_mode(db, user):
    other_user = User(google_sub="s3", email="e3@x.com", display_name="T3")
    db.add(other_user)
    db.commit()
    db.refresh(other_user)

    orders = [
        _db_order(user.id, "TCS", Side.buy, 10, 4000.0, mode=Mode.paper, minutes_after_t0=0),
        _db_order(user.id, "TCS", Side.sell, 10, 4200.0, mode=Mode.paper, minutes_after_t0=1),
        _db_order(user.id, "TCS", Side.buy, 5, 4000.0, mode=Mode.virtual, minutes_after_t0=2),
        _db_order(user.id, "TCS", Side.sell, 5, 3900.0, mode=Mode.virtual, minutes_after_t0=3),
        _db_order(other_user.id, "TCS", Side.buy, 1, 4000.0, mode=Mode.paper, minutes_after_t0=4),
        _db_order(other_user.id, "TCS", Side.sell, 1, 5000.0, mode=Mode.paper, minutes_after_t0=5),
    ]
    db.add_all(orders)
    db.commit()

    user_paper = get_cached_realizations(db, user.id, Mode.paper)
    user_virtual = get_cached_realizations(db, user.id, Mode.virtual)
    other_paper = get_cached_realizations(db, other_user.id, Mode.paper)

    assert len(user_paper) == 1 and user_paper[0].amount == pytest.approx(10 * (4200.0 - 4000.0))
    assert len(user_virtual) == 1 and user_virtual[0].amount == pytest.approx(5 * (3900.0 - 4000.0))
    assert len(other_paper) == 1 and other_paper[0].amount == pytest.approx(1 * (5000.0 - 4000.0))

    # A repeat call with nothing new must return the same list, not
    # double-append the same realizations.
    assert get_cached_realizations(db, user.id, Mode.paper) == user_paper
