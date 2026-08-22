"""Tests for simulation-loop correctness -- specifically fill attribution,
which is the part most likely to silently corrupt every P&L number if wrong.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

from agents import MarketMaker
from engine import Fill
from simulate import owner_of_order_id, _route_fills


def test_owner_of_order_id_inverts_the_encoding():
    # Every agent mints ids as trader_id * 10_000_000 + seq.
    assert owner_of_order_id(1 * 10_000_000 + 47) == 1
    assert owner_of_order_id(200 * 10_000_000 + 1) == 200
    assert owner_of_order_id(999 * 10_000_000 + 999_999) == 999


def test_maker_resting_buy_gets_hit_by_a_sell_taker():
    """Maker's resting BUY order gets filled by an aggressing SELL taker ->
    the maker just bought, so inventory should go UP, not down."""
    maker = MarketMaker(trader_id=1, tick_size=0.01)
    maker_by_id = {1: maker}
    fill = Fill(seq=1, taker_id=200_000_001, maker_id=10_000_005, px=10000, qty=50, taker_side="sell")

    prices, times = [], []
    class R:
        fills = [fill]
    _route_fills(R(), maker_by_id, 0.01, prices, times, t=0)

    assert maker.inventory == 50, "maker's resting buy was hit -> inventory must increase"
    assert maker.cash == -50 * 10000


def test_maker_resting_sell_gets_hit_by_a_buy_taker():
    """Maker's resting SELL gets filled by an aggressing BUY -> maker sold,
    inventory should go DOWN."""
    maker = MarketMaker(trader_id=1, tick_size=0.01)
    maker_by_id = {1: maker}
    fill = Fill(seq=1, taker_id=200_000_001, maker_id=10_000_005, px=10000, qty=30, taker_side="buy")

    prices, times = [], []
    class R:
        fills = [fill]
    _route_fills(R(), maker_by_id, 0.01, prices, times, t=0)

    assert maker.inventory == -30, "maker's resting sell was hit -> inventory must decrease"
    assert maker.cash == 30 * 10000


def test_maker_as_taker_moves_inventory_in_its_own_side_direction():
    """If the market maker were ever the AGGRESSOR (not the case in the
    current simulation loop, but the routing function must handle it
    correctly regardless), inventory should move in the direction of its
    own order's side, not the resting side's."""
    maker = MarketMaker(trader_id=1, tick_size=0.01)
    maker_by_id = {1: maker}
    fill = Fill(seq=1, taker_id=10_000_009, maker_id=200_000_002, px=10000, qty=20, taker_side="buy")

    prices, times = [], []
    class R:
        fills = [fill]
    _route_fills(R(), maker_by_id, 0.01, prices, times, t=0)

    assert maker.inventory == 20, "maker was the taker on a BUY -> inventory increases"


def test_fill_between_two_non_maker_traders_does_not_touch_maker_state():
    maker = MarketMaker(trader_id=1, tick_size=0.01)
    maker_by_id = {1: maker}
    fill = Fill(seq=1, taker_id=200_000_001, maker_id=100_000_005, px=10000, qty=40, taker_side="buy")

    prices, times = [], []
    class R:
        fills = [fill]
    _route_fills(R(), maker_by_id, 0.01, prices, times, t=3)

    assert maker.inventory == 0 and maker.cash == 0.0
    assert prices == [10000.0] and times == [3], "trade tape must still record the fill"


def test_mark_to_market_reflects_inventory_and_cash():
    maker = MarketMaker(trader_id=1, tick_size=0.01)
    maker.inventory = 100
    maker.cash = -50_000  # bought 100 @ 500 ticks
    # Raw tick-unit expression: cash + inventory*mid = -50000 + 100*520 = 2000
    # tick_size=0.01 converts that to real currency units: 2000 * 0.01 = 20.0
    assert maker.mark_to_market(520) == pytest.approx(20.0)


def test_mark_to_market_units_scale_with_tick_size():
    """A P&L of 2000 raw ticks must read very differently at tick_size=1
    (a market quoted in whole currency units) vs tick_size=0.01 (quoted in
    cents) -- this is exactly the bug that produced a 100x-inflated P&L
    number before mark_to_market applied the conversion."""
    cheap_ticks = MarketMaker(trader_id=1, tick_size=0.01)
    cheap_ticks.inventory, cheap_ticks.cash = 100, -50_000
    whole_units = MarketMaker(trader_id=1, tick_size=1.0)
    whole_units.inventory, whole_units.cash = 100, -50_000

    assert whole_units.mark_to_market(520) == pytest.approx(2000.0)
    assert cheap_ticks.mark_to_market(520) == pytest.approx(20.0)


def test_market_maker_never_self_crosses_across_a_refresh():
    """Regression test for the post-before-cancel self-cross risk: if a
    refresh's new bid would cross the maker's own still-resting OLD ask (or
    vice versa), the guard must cancel the stale opposite-side order first
    rather than let the engine match the maker against itself.

    Drives the real Engine + MarketMaker through a sequence where the mid
    jumps sharply between refreshes -- exactly the condition that triggers a
    self-cross -- and asserts inventory only ever moves from EXTERNAL fills,
    by checking cash/inventory bookkeeping stays internally consistent
    (mark_to_market must never reflect a "trade" that was actually the
    maker filling itself, which would double the apparent quantity moved
    with no counterparposition price risk to justify it).
    """
    from engine import Engine
    from agents import MarketMaker

    maker = MarketMaker(trader_id=1, tick_size=0.01, base_half_spread_ticks=3.0,
                         inventory_skew_ticks_per_unit=0.0, quote_size=50)

    with Engine(min_px=1, max_px=20_000, tick=1, capacity=1 << 16) as eng:
        maker.refresh_quotes(eng, mid_ticks=10_000, vol_estimate=0.0)
        # A large, sudden mid jump -- the kind of move that would make a
        # naively-computed new bid cross the still-resting old ask if the
        # guard weren't in place (old ask sat near 10003; new mid of 10500
        # would want a bid well above that).
        maker.refresh_quotes(eng, mid_ticks=10_500, vol_estimate=0.0)

        eng.check_invariants()  # the book itself must still be well-formed
        bid, ask = eng.best_bid(), eng.best_ask()
        assert bid is not None and ask is not None
        assert bid[0] < ask[0], "maker's own two live quotes must never be crossed"

        # No external participant traded, so the maker must show ZERO
        # inventory change -- any nonzero value here means it filled itself.
        assert maker.inventory == 0, (
            f"maker inventory changed to {maker.inventory} with no external "
            "counterparty -- indicates a self-trade slipped through"
        )


def test_run_simulation_honors_injected_maker():
    """A parameter sweep is only valid if the injected maker is what
    actually trades, not a maker run_simulation constructed internally and
    silently used instead."""
    from simulate import run_simulation
    from agents import MarketMaker

    custom = MarketMaker(trader_id=1, tick_size=0.01, quote_size=999_999)
    res = run_simulation(steps=5, seed=0, maker=custom)
    # The injected instance is the SAME object the simulation mutated --
    # its inventory/cash must reflect the run, proving it (not some other
    # maker) was actually used.
    assert custom.inventory == res.mm_inventory_path[-1]


def test_run_simulation_accepts_a_list_of_makers():
    """Multi-maker support: passing a list of makers must run all of them,
    with correctly-attributed per-maker inventory/P&L, and the aggregate
    paths must equal the sum across makers at every step."""
    from simulate import run_simulation

    m1 = MarketMaker(trader_id=1, tick_size=0.01, base_half_spread_ticks=3.0)
    m2 = MarketMaker(trader_id=2, tick_size=0.01, base_half_spread_ticks=5.0)
    res = run_simulation(steps=200, seed=3, maker=[m1, m2])

    assert len(res.per_maker_final_inventory) == 2
    assert len(res.per_maker_final_pnl) == 2
    assert res.per_maker_final_inventory[0] == m1.inventory
    assert res.per_maker_final_inventory[1] == m2.inventory
    assert res.mm_inventory_path[-1] == m1.inventory + m2.inventory


def test_run_simulation_rejects_duplicate_trader_ids():
    from simulate import run_simulation

    m1 = MarketMaker(trader_id=1, tick_size=0.01)
    m2 = MarketMaker(trader_id=1, tick_size=0.01)  # same id -- invalid
    with pytest.raises(ValueError):
        run_simulation(steps=10, seed=0, maker=[m1, m2])


def test_route_fills_updates_both_sides_of_an_inter_maker_trade():
    """Regression test for the if/elif bug fixed alongside multi-maker
    support: if maker A's resting order is hit by maker B's aggressive
    order, BOTH inventories must update -- the old if/elif logic would
    silently only update one of them."""
    maker_a = MarketMaker(trader_id=1, tick_size=0.01)
    maker_b = MarketMaker(trader_id=2, tick_size=0.01)
    maker_by_id = {1: maker_a, 2: maker_b}

    # maker_a's resting buy (order 10_000_005) hit by maker_b's aggressive
    # sell (order 20_000_003).
    fill = Fill(seq=1, taker_id=20_000_003, maker_id=10_000_005, px=10000, qty=40, taker_side="sell")
    prices, times = [], []
    class R:
        fills = [fill]
    _route_fills(R(), maker_by_id, 0.01, prices, times, t=0)

    assert maker_a.inventory == 40, "resting side (maker_a) was hit by a sell -> bought, inventory up"
    assert maker_b.inventory == -40, "aggressing side (maker_b) sold -> inventory down"
