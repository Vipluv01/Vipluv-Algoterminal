"""Tests for LiveSim -- the interactive simulation driving the browser demo.

These test the SAME class the WebSocket server uses directly (not through a
socket), since the interesting logic (state transitions, JSON-safety of
every step's output) has nothing to do with the network layer.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

from websockets.datastructures import Headers
from websockets.http11 import Request

from demo_server import (
    MAX_INFORMED_TRADERS,
    MAX_ORDER_QTY,
    MAX_OPEN_ORDERS_PER_HUMAN,
    HumanTrader,
    LiveSim,
    serve_static_or_upgrade,
)


def test_step_produces_json_serializable_output():
    sim = LiveSim(seed=1)
    try:
        for _ in range(20):
            msg = sim.step()
            json.dumps(msg)  # raises if anything (e.g. a numpy scalar) leaked through
    finally:
        sim.close()


def test_step_count_increments():
    sim = LiveSim(seed=1)
    try:
        assert sim.step_count == 0
        sim.step()
        assert sim.step_count == 1
        sim.step()
        assert sim.step_count == 2
    finally:
        sim.close()


def test_add_informed_trader_increases_population():
    sim = LiveSim(seed=1)
    try:
        n0 = len(sim.informed_traders)
        sim.add_informed_trader()
        assert len(sim.informed_traders) == n0 + 1
        # The new trader must have a distinct id from every existing one --
        # a collision would mean its fills get misattributed via
        # owner_of_order_id.
        ids = [t.trader_id for t in sim.informed_traders]
        assert len(ids) == len(set(ids))
    finally:
        sim.close()


def test_set_volatility_is_clamped_to_a_sane_range():
    sim = LiveSim(seed=1)
    try:
        sim.set_volatility(1000.0)
        assert sim.fundamental.sigma <= 2.0
        sim.set_volatility(-5.0)
        assert sim.fundamental.sigma >= 0.01
    finally:
        sim.close()


def test_depth_and_maker_stats_present_every_step():
    """The frontend indexes msg["maker"]["inventory"] etc. unconditionally --
    these keys must never be missing, even on step 1 before any fills."""
    sim = LiveSim(seed=2)
    try:
        msg = sim.step()
        assert "bids" in msg and "asks" in msg and "trades" in msg
        assert set(msg["maker"].keys()) == {"inventory", "pnl", "spread_ticks"}
    finally:
        sim.close()


def test_add_informed_trader_is_capped():
    """Regression test: add_informed_trader() is reachable by any connected
    browser tab once this server is public, not just a trusted operator --
    an earlier version had no bound, so a single misbehaving or malicious
    client could spam this command and grow per-step work without limit for
    every visitor watching the same live demo."""
    sim = LiveSim(seed=1)
    try:
        for _ in range(MAX_INFORMED_TRADERS + 20):
            sim.add_informed_trader()
        assert len(sim.informed_traders) == MAX_INFORMED_TRADERS
    finally:
        sim.close()


def _request(path: str, upgrade: str | None) -> Request:
    headers = Headers()
    if upgrade is not None:
        headers["Upgrade"] = upgrade
    return Request(path=path, headers=headers)


def test_websocket_upgrade_request_falls_through_to_the_handshake():
    """Regression test for a real bug caught by hand while wiring up single-
    port deployment: an earlier version matched on request.path alone ("/"),
    which also matches the WebSocket handshake's own GET request -- so the
    server answered the handshake with a raw HTML body instead of letting
    it proceed, and the browser correctly rejected that as a failed upgrade.
    The page loaded fine (misleadingly looking like success) while the
    entire live feed silently never connected. Only the Upgrade header
    distinguishes the two, so this must return None (defer to the library's
    own handshake handling) whenever it's present."""
    req = _request("/", upgrade="websocket")
    result = asyncio.run(_maybe_await(serve_static_or_upgrade(None, req)))
    assert result is None


def test_plain_get_of_root_serves_the_static_page():
    req = _request("/", upgrade=None)
    result = asyncio.run(_maybe_await(serve_static_or_upgrade(None, req)))
    assert result is not None
    assert result.status_code == 200
    assert b"<!DOCTYPE html>" in bytes(result.body)


def test_healthz_does_not_require_the_full_page_body():
    req = _request("/healthz", upgrade=None)
    result = asyncio.run(_maybe_await(serve_static_or_upgrade(None, req)))
    assert result is not None
    assert result.status_code == 200
    assert bytes(result.body) == b"ok"


async def _maybe_await(value):
    return await value if asyncio.iscoroutine(value) else value


# --- Human trading (order entry, cancel, fill attribution) ---


def test_register_human_gets_a_reserved_trader_id_distinct_from_bots():
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        assert h.trader_id >= 10_000
        assert h.trader_id not in sim.maker_ids
        assert h.trader_id not in {t.trader_id for t in sim.noise_traders + sim.informed_traders}
    finally:
        sim.close()


def test_submit_order_rejects_invalid_side_and_type_and_qty():
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        assert sim.submit_human_order(h, side="up", qty=1, px=100.0, order_type="limit")["ok"] is False
        assert sim.submit_human_order(h, side="buy", qty=1, px=100.0, order_type="stop")["ok"] is False
        assert sim.submit_human_order(h, side="buy", qty=0, px=100.0, order_type="limit")["ok"] is False
        assert sim.submit_human_order(h, side="buy", qty=-5, px=100.0, order_type="limit")["ok"] is False
        assert sim.submit_human_order(h, side="buy", qty=MAX_ORDER_QTY + 1, px=100.0, order_type="limit")["ok"] is False
    finally:
        sim.close()


def test_limit_order_requires_a_positive_price():
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        assert sim.submit_human_order(h, side="buy", qty=1, px=None, order_type="limit")["ok"] is False
        assert sim.submit_human_order(h, side="buy", qty=1, px=0.0, order_type="limit")["ok"] is False
        assert sim.submit_human_order(h, side="buy", qty=1, px=-1.0, order_type="limit")["ok"] is False
    finally:
        sim.close()


def test_market_order_does_not_require_a_price():
    """A market buy walks the seeded resting sell at start_ticks+5 -- no
    price needed, unlike a limit order."""
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        result = sim.submit_human_order(h, side="buy", qty=5, px=None, order_type="market")
        assert result["ok"] is True
        assert h.inventory == 5
        assert h.open_orders == {}
    finally:
        sim.close()


def test_resting_limit_order_appears_in_open_orders_and_not_yet_filled():
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        # Priced far below the touch -- a passive resting buy, not a cross.
        result = sim.submit_human_order(h, side="buy", qty=10, px=50.0, order_type="limit")
        assert result["ok"] is True
        assert result["filled_qty"] == 0
        assert h.inventory == 0
        assert len(h.open_orders) == 1
        oid = result["order_id"]
        assert h.open_orders[oid] == {"side": "buy", "px": 50.0, "qty": 10}
    finally:
        sim.close()


def test_cancel_own_resting_order_removes_it_from_open_orders_and_the_book():
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        result = sim.submit_human_order(h, side="buy", qty=10, px=50.0, order_type="limit")
        oid = result["order_id"]

        cancel_result = sim.cancel_human_order(h, oid)
        assert cancel_result["ok"] is True
        assert h.open_orders == {}
        # The engine itself must agree the order is gone -- cancelling twice
        # should now fail (nothing left to cancel), proving this isn't just
        # local bookkeeping drifting from the real book state.
        assert sim.eng.cancel(oid) != "none"
    finally:
        sim.close()


def test_cancel_rejects_an_order_id_this_human_does_not_own():
    sim = LiveSim(seed=1)
    try:
        h1 = sim.register_human()
        h2 = sim.register_human()
        result = sim.submit_human_order(h1, side="buy", qty=10, px=50.0, order_type="limit")
        oid = result["order_id"]

        stolen = sim.cancel_human_order(h2, oid)
        assert stolen["ok"] is False
        assert oid in h1.open_orders, "a rejected cancel from a non-owner must leave the real owner's order untouched"
    finally:
        sim.close()


def test_open_orders_are_capped_per_human():
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        # LiveSim's book only spans [s0*0.5, s0*2.0] = [50, 200] here --
        # stay inside that range so a rejection can only mean the cap fired,
        # not that the price was simply out of bounds.
        for i in range(MAX_OPEN_ORDERS_PER_HUMAN):
            result = sim.submit_human_order(h, side="sell", qty=1, px=150.0 + i, order_type="limit")
            assert result["ok"] is True
        over_limit = sim.submit_human_order(h, side="sell", qty=1, px=199.0, order_type="limit")
        assert over_limit["ok"] is False
        assert len(h.open_orders) == MAX_OPEN_ORDERS_PER_HUMAN
    finally:
        sim.close()


def test_fill_against_a_resting_human_order_updates_inventory_and_shrinks_open_order():
    """A second human's aggressive market sell should hit the first human's
    resting buy -- proving _route()'s fill attribution works for humans on
    BOTH the maker and taker side, not just when a human is the one calling
    submit_human_order directly."""
    sim = LiveSim(seed=1)
    try:
        maker = sim.register_human()
        taker = sim.register_human()

        # Must outbid the seed liquidity (resting buy at 99.95, see
        # LiveSim.__post_init__) to actually become the best bid -- a
        # market sell always hits the best bid first, so a worse-priced
        # resting order here would get skipped in favor of the seed order,
        # silently making this test check the wrong thing.
        rest = sim.submit_human_order(maker, side="buy", qty=10, px=99.99, order_type="limit")
        oid = rest["order_id"]

        hit = sim.submit_human_order(taker, side="sell", qty=4, px=None, order_type="market")
        assert hit["ok"] is True
        assert hit["filled_qty"] == 4

        assert maker.inventory == 4
        assert taker.inventory == -4
        assert maker.open_orders[oid]["qty"] == 6, "partial fill must shrink the resting qty, not clear it"
    finally:
        sim.close()


def test_unregister_human_cancels_their_resting_orders_on_the_real_book():
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        sim.submit_human_order(h, side="buy", qty=10, px=50.0, order_type="limit")
        assert len(h.open_orders) == 1

        sim.unregister_human(h)
        assert h.trader_id not in sim.humans_by_id
        assert h.open_orders == {}
    finally:
        sim.close()


def test_order_result_echoes_the_requested_order_type():
    """Regression test: found live in the browser demo. A market order that
    filled nothing (empty bid/ask side -- common here, see
    sim/KNOWN_ISSUES.md) needs a different frontend message than a limit
    order resting, since a market order can never rest. Distinguishing them
    client-side requires order_type actually being in the response --
    it was missing until this was caught."""
    sim = LiveSim(seed=1)
    try:
        h = sim.register_human()
        limit_result = sim.submit_human_order(h, side="buy", qty=1, px=50.0, order_type="limit")
        assert limit_result["order_type"] == "limit"
        market_result = sim.submit_human_order(h, side="sell", qty=1, px=None, order_type="market")
        assert market_result["order_type"] == "market"
    finally:
        sim.close()


def test_humantrader_mark_to_market_matches_markermaker_style_accounting():
    """Same formula MarketMaker uses (agents.py): cash plus inventory
    priced at the current mid, converted from ticks at the boundary."""
    h = HumanTrader(trader_id=10_001, tick_size=0.01)
    h.on_fill("buy", 10, 10_000)  # bought 10 @ 100.00
    assert h.cash == -100_000.0
    pnl_at_same_price = h.mark_to_market(10_000)
    assert pnl_at_same_price == pytest.approx(0.0)
    pnl_after_rally = h.mark_to_market(10_100)  # price rose to 101.00
    assert pnl_after_rally == pytest.approx(10.0)
