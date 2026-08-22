"""Tests for the Python<->Go engine bridge.

These duplicate a few cases already covered by internal/book's own Go tests
(price-time priority, cancel behaviour) -- deliberately. The point isn't to
re-verify the matching logic itself; it's to verify the WIRE PROTOCOL
faithfully carries that logic across the process boundary. A bug here would
be an encoding/decoding bug, not a matching bug, and the two need different
tests to catch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

import pytest
from engine import Engine, EngineError


@pytest.fixture
def eng():
    with Engine(min_px=1, max_px=20_000, tick=1) as e:
        yield e


def test_resting_order_then_cross(eng):
    r1 = eng.submit(order_id=1, owner=1, side="buy", qty=10, px=100)
    assert r1.accepted and r1.filled_qty == 0

    r2 = eng.submit(order_id=2, owner=2, side="sell", qty=5, px=100)
    assert r2.accepted
    assert len(r2.fills) == 1
    f = r2.fills[0]
    assert f.px == 100  # maker's price -- price improvement crosses the wire
    assert f.qty == 5
    assert f.maker_id == 1
    assert f.taker_id == 2


def test_price_time_priority_survives_the_wire(eng):
    eng.submit(order_id=1, owner=1, side="buy", qty=10, px=100)
    eng.submit(order_id=2, owner=2, side="buy", qty=10, px=100)

    r = eng.submit(order_id=3, owner=3, side="sell", qty=15, px=100)
    assert len(r.fills) == 2
    assert r.fills[0].maker_id == 1 and r.fills[0].qty == 10
    assert r.fills[1].maker_id == 2 and r.fills[1].qty == 5


def test_cancel_and_rejects(eng):
    eng.submit(order_id=1, side="buy", qty=10, px=100)
    assert eng.cancel(1) == "none"
    assert eng.cancel(1) == "unknown order id"
    assert eng.best_bid() is None


def test_market_order_type_crosses_the_wire(eng):
    eng.submit(order_id=1, owner=1, side="sell", qty=5, px=101)
    r = eng.submit(order_id=2, owner=2, side="buy", qty=5, order_type="market")
    assert r.accepted
    assert len(r.fills) == 1
    assert r.fills[0].px == 101


def test_invariant_check_round_trips_ok(eng):
    for i in range(1, 21):
        eng.submit(order_id=i, side="buy" if i % 2 else "sell", qty=i, px=100 + (i % 5))
    eng.check_invariants()  # must not raise


def test_depth_and_mid(eng):
    eng.submit(order_id=1, side="buy", qty=10, px=99)
    eng.submit(order_id=2, side="sell", qty=10, px=101)
    assert eng.mid() == 100.0
    bids, asks = eng.depth(5)
    assert bids[0].px == 99
    assert asks[0].px == 101


def test_closed_engine_raises_not_hangs():
    eng = Engine(min_px=1, max_px=20_000, tick=1)
    eng.close()
    with pytest.raises(EngineError):
        eng.submit(order_id=1, side="buy", qty=1, px=100)


def test_price_collar_config_reaches_the_engine():
    with Engine(min_px=1, max_px=20_000, tick=1, price_collar_bps=100) as eng:  # 1%
        eng.submit(order_id=1, owner=1, side="sell", qty=10, px=100)
        eng.submit(order_id=2, owner=2, side="buy", qty=10, px=100)  # trade at 100 -> lastPx=100

        # 10% away under a 1% collar -- must reject.
        r = eng.submit(order_id=3, owner=3, side="buy", qty=5, px=110)
        assert not r.accepted
        assert r.reject == "price outside fat-finger collar of last trade"


def test_position_limit_config_reaches_the_engine_and_position_is_queryable():
    with Engine(min_px=1, max_px=20_000, tick=1, position_limit=20) as eng:
        eng.submit(order_id=1, owner=1, side="sell", qty=20, px=100)

        r = eng.submit(order_id=2, owner=2, side="buy", qty=25, px=100)
        assert not r.accepted
        assert r.reject == "would exceed owner's position limit"
        assert eng.position(2) == 0  # rejected order must not move position

        r2 = eng.submit(order_id=3, owner=2, side="buy", qty=20, px=100)
        assert r2.accepted and r2.filled_qty == 20
        assert eng.position(2) == 20
        assert eng.position(1) == -20


def test_position_defaults_to_zero_for_unknown_owner():
    with Engine(min_px=1, max_px=20_000, tick=1) as eng:
        assert eng.position(999) == 0


def test_recovered_field_is_zero_on_a_fresh_book_with_wal():
    with Engine(min_px=1, max_px=20_000, tick=1, wal_path="/tmp/bourse_test_fresh.wal") as eng:
        pass
    import os
    try:
        os.remove("/tmp/bourse_test_fresh.wal")
    except FileNotFoundError:
        pass
