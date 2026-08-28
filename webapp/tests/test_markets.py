"""Verifies named, per-symbol markets are genuinely independent -- the
direct fix for a real complaint: an earlier version ran one anonymous
synthetic instrument with no name, when a serious multi-symbol terminal
needs clearly-identified tradable symbols."""

import numpy as np
import pytest

from app.markets import HUMAN_USER_OWNER_ID, MarketRegistry, NAMED_INSTRUMENTS, SymbolMarket


def test_human_user_owner_id_does_not_collide_with_the_market_maker():
    """Found while building the leaderboard: the human user's own orders
    and SymbolMarket's own MarketMaker bot must be distinguishable by
    owner id in the engine's fill/position records, or a leaderboard (or
    any other per-owner P&L split) silently mixes the two."""
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        maker_id = registry.markets["ICICIBANK"].maker.trader_id
        assert HUMAN_USER_OWNER_ID != maker_id
    finally:
        registry.close()


def test_named_instruments_include_the_icici_hdfc_pair():
    """pairs_cointegration.py was validated against exactly this pair
    (icici_mean_reversion) -- it must actually be tradable here."""
    assert "ICICIBANK" in NAMED_INSTRUMENTS
    assert "HDFCBANK" in NAMED_INSTRUMENTS


def test_registry_creates_one_independent_market_per_symbol():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0, "HDFCBANK": 1650.0}, seed=0)
    try:
        assert set(registry.markets.keys()) == {"ICICIBANK", "HDFCBANK"}
        assert registry.markets["ICICIBANK"].s0 == 1250.0
        assert registry.markets["HDFCBANK"].s0 == 1650.0
    finally:
        registry.close()


def test_stepping_the_registry_advances_every_symbol_independently():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0, "HDFCBANK": 1650.0}, seed=0)
    try:
        for _ in range(20):
            prices = registry.step_all()
        assert set(prices.keys()) == {"ICICIBANK", "HDFCBANK"}
        icici_history = registry.prices("ICICIBANK")
        hdfc_history = registry.prices("HDFCBANK")
        assert len(icici_history) == 21  # seed price + 20 steps
        assert len(hdfc_history) == 21
        # Different starting prices and independent seeds/agent populations
        # -- the two series must not be identical.
        assert not np.array_equal(icici_history, hdfc_history)
    finally:
        registry.close()


def test_unknown_symbol_raises_a_clear_error():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        with pytest.raises(KeyError):
            registry.prices("NOTASYMBOL")
    finally:
        registry.close()


def test_next_order_id_is_unique_and_increasing():
    m = SymbolMarket(symbol="TCS", s0=4000.0, seed=2)
    try:
        ids = [m.next_order_id() for _ in range(5)]
        assert ids == sorted(set(ids))
        assert len(set(ids)) == 5
    finally:
        m.close()


def test_current_price_matches_the_latest_price_history_entry():
    m = SymbolMarket(symbol="TCS", s0=4000.0, seed=2)
    try:
        assert m.current_price == 4000.0
        m.step()
        assert m.current_price == m.price_history[-1]
    finally:
        m.close()


def test_registry_current_prices_covers_every_symbol():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0, "HDFCBANK": 1650.0}, seed=0)
    try:
        prices = registry.current_prices()
        assert prices == {"ICICIBANK": 1250.0, "HDFCBANK": 1650.0}
    finally:
        registry.close()


def test_registry_getitem_returns_the_named_symbol_market():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        assert registry["ICICIBANK"].symbol == "ICICIBANK"
        with pytest.raises(KeyError):
            registry["NOPE"]
    finally:
        registry.close()


def test_symbol_market_price_history_starts_at_its_own_seed_price():
    m = SymbolMarket(symbol="RELIANCE", s0=2900.0, seed=1)
    try:
        assert m.price_history == [2900.0]
        m.step()
        assert len(m.price_history) == 2
    finally:
        m.close()


def test_recorded_fill_owner_fields_resolve_the_real_submitting_owner():
    """taker_id/maker_id are ENGINE ORDER ids, not owner ids (see
    test_recent_fills_are_recorded_regardless_of_caller's own comment on
    this) -- taker_owner/maker_owner exist specifically so a consumer like
    the leaderboard can attribute a fill to WHO traded, not just which
    order ids were involved."""
    m = SymbolMarket(symbol="ICICIBANK", s0=1250.0, seed=9)
    try:
        # Seed liquidity itself is owner 999 -- crossing it directly proves
        # the lookup works for an order submitted before this test's own
        # order, not just "the most recent submit."
        order_id = m.next_order_id()
        result = m.eng.submit(order_id=order_id, side="buy", qty=5, order_type="market", owner=42)
        assert result.filled_qty > 0

        fill = next(f for f in m.recent_fills if f.taker_id == order_id)
        assert fill.taker_owner == 42
        assert fill.maker_owner == 999  # the seed sell order's owner
    finally:
        m.close()


def test_recent_fills_are_recorded_regardless_of_caller():
    """recent_fills is populated by wrapping eng.submit once, so it must
    capture fills from simulated agents (via step()) AND from a direct
    caller using market.eng.submit -- which is exactly how
    routers/orders.py submits the web app's own orders."""
    m = SymbolMarket(symbol="ICICIBANK", s0=1250.0, seed=4)
    try:
        for _ in range(30):
            m.step()
        assert len(m.recent_fills) > 0, "ordinary agent activity should have produced fills"
        # Not "len() grew" -- at this fill rate the 500-entry cap is reached
        # quickly (500 fills was observed inside 200 steps in exploration),
        # and once full, length cannot increase further; a new fill instead
        # evicts the oldest. So the identifying signal is this order's own
        # ENGINE ORDER ID: Fill.taker_id/maker_id are order ids, not owner
        # ids (internal/book/types.go), so the id assigned to this specific
        # submit is what a match is keyed on, not the owner passed in.
        direct_order_id = m.next_order_id()

        # A market order needs the opposing side to be non-empty to fill at
        # all, and this book is genuinely one-sided part of the time (see
        # sim/KNOWN_ISSUES.md -- ~97% of individual steps are one-sided).
        # Retry a few steps rather than assume any single moment has
        # liquidity; this is only testing that a fill gets recorded when one
        # happens, not asserting anything about simulation dynamics.
        result = None
        for _ in range(30):
            result = m.eng.submit(
                order_id=direct_order_id, side="buy", qty=5,
                order_type="market", owner=777_777,
            )
            if result.filled_qty > 0:
                break
            direct_order_id = m.next_order_id()  # a fresh id per attempt -- ids must not repeat
            m.step()
        assert result is not None and result.filled_qty > 0, "no fill after 30 attempts -- book stayed empty on one side"

        matches = [f for f in m.recent_fills if f.taker_id == direct_order_id]
        assert matches, "a directly-submitted fill was not recorded in recent_fills"
        latest = matches[-1]
        assert latest.symbol == "ICICIBANK"
        assert latest.qty > 0
        # px is converted to real currency (tick_size applied), not a raw tick.
        assert latest.px > 100  # well above a raw tick count at this price level
    finally:
        m.close()


def test_recent_fills_is_bounded():
    """Must not grow without limit across a long-running process or a long
    headless backtest (Phase 3)."""
    m = SymbolMarket(symbol="ICICIBANK", s0=1250.0, seed=5)
    try:
        for _ in range(600):
            m.step()
        assert len(m.recent_fills) <= 500
    finally:
        m.close()


def test_recent_volume_has_one_entry_per_step_and_matches_fill_qty():
    """recent_volume is derived from the SAME fills recent_fills records
    (not a separate eng.stats() call, to avoid an extra IPC round trip per
    step -- see the comment in SymbolMarket.step()), so the two must agree:
    a step's recorded volume must equal the summed qty of fills tagged with
    that step.

    Only the MOST RECENT step is checked, deliberately: recent_fills caps at
    500 fills (not 500 steps), and a single step here was observed producing
    over 200 fills on its own, so older steps' fills can already be evicted
    by the time this runs -- reconstructing volume from what remains would
    then legitimately undercount them. The newest step's fills are always
    intact (eviction happens from the deque's other end), so it's the only
    step this cross-check can make safely.
    """
    m = SymbolMarket(symbol="ICICIBANK", s0=1250.0, seed=6)
    try:
        for _ in range(50):
            m.step()
        assert len(m.recent_volume) == 50

        last_step = m._step_count
        expected = sum(f.qty for f in m.recent_fills if f.step == last_step)
        assert m.recent_volume[-1] == expected, (
            f"last step's recorded volume {m.recent_volume[-1]} != "
            f"summed fill qty for that step {expected}"
        )
    finally:
        m.close()


def test_risk_config_is_generous_enough_not_to_touch_normal_simulation():
    """price_collar_bps and position_limit are threaded through to the
    engine (previously both defaulted to 0/off -- wireConfig supported them,
    markets.py just never passed them).

    Both thresholds were chosen with real margin above measured agent
    behaviour (see the comment in markets.py), specifically so they cannot
    silently distort the simulation's own price dynamics -- the same
    failure mode as the stale-mid fallback bug in sim/KNOWN_ISSUES.md, just
    from a different cause. This test is the tripwire: if the simulation's
    behaviour ever changes enough for either check to start binding on
    ordinary agent flow, this fails loudly instead of quietly.
    """
    m = SymbolMarket(symbol="ICICIBANK", s0=1250.0, seed=3)
    try:
        for _ in range(400):
            m.step()

        stats = m.eng.stats()
        assert stats["trades"] > 0, "sanity: the market should actually be trading"

        # None of that ordinary agent activity should have needed the collar
        # or the position limit to intervene -- both are for catching
        # genuinely abnormal orders, not shaping normal ones.
        for owner in [1, 999, *range(100, 120), *range(200, 205)]:
            assert abs(m.eng.position(owner)) < 20_000, (
                f"owner {owner} position {m.eng.position(owner)} is within noise "
                "of position_limit=20_000 -- the margin has eroded"
            )
    finally:
        m.close()


def test_risk_config_rejects_a_genuine_fat_finger():
    """The other half of the same check: the collar must still actually DO
    something. A limit order priced far outside any plausible market move
    (20% off the last trade) must be rejected, not silently accepted."""
    m = SymbolMarket(symbol="ICICIBANK", s0=1250.0, seed=3)
    try:
        for _ in range(50):
            m.step()

        last = m.eng.last_px()
        assert last is not None, "sanity: something must have traded by now"

        result = m.eng.submit(
            order_id=m.next_order_id(), side="buy", qty=10,
            px=int(last * 1.20), owner=88_888,
        )
        assert result.reject != "none", "a 20%-off order should be rejected by the price collar"
    finally:
        m.close()
