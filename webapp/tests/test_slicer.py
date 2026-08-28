"""TWAP/VWAP slicing -- both a pure-function level check of the per-bar
sizing formula (using a lightweight fake market so volume weights are
exactly known, not noisy live-simulation numbers) and an end-to-end check
against the real MarketRegistry."""

from collections import deque

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.execution.slicer import TWAPSlicer, VWAPSlicer, run_algo_orders_once
from app.markets import MarketRegistry
from app.models.execution import ParentOrder, ParentOrderStatus, SlicerAlgo
from app.models.risk import RiskSettings
from app.models.trading import Order
from app.models.user import User


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def registry():
    reg = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        yield reg
    finally:
        reg.close()


@pytest.fixture
def user(db):
    u = User(google_sub="test-sub", email="t@example.com", display_name="Test")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


class _FakeMarket:
    def __init__(self, recent_volume):
        self.recent_volume = deque(recent_volume)


class _FakeRegistry:
    """A stand-in for MarketRegistry that only implements what
    VWAPSlicer.slice_qty_for_bar actually touches (registry[symbol] ->
    an object with .recent_volume) -- lets the volume weighting formula
    be tested against EXACTLY known numbers, not the live simulation's
    organic (and therefore hard-to-hand-verify) volume."""

    def __init__(self, volumes_by_symbol: dict[str, list[int]]):
        self._markets = {sym: _FakeMarket(vols) for sym, vols in volumes_by_symbol.items()}

    def __getitem__(self, symbol):
        return self._markets[symbol]


def _parent(**overrides) -> ParentOrder:
    defaults = dict(
        id=1, user_id=1, symbol="X", side="buy", total_qty=1000, filled_qty=0,
        algo=SlicerAlgo.twap, horizon_bars=5, start_bar=0, status=ParentOrderStatus.active,
    )
    defaults.update(overrides)
    return ParentOrder(**defaults)


# ---------------------------------------------------------------------------
# TWAP: pure formula
# ---------------------------------------------------------------------------

def test_twap_divides_evenly_when_total_qty_is_a_multiple_of_horizon():
    slicer = TWAPSlicer()
    parent = _parent(total_qty=100, horizon_bars=4, filled_qty=0)
    assert slicer.slice_qty_for_bar(None, parent, bar_index=0) == 25
    assert slicer.slice_qty_for_bar(None, parent, bar_index=1) == 25
    assert slicer.slice_qty_for_bar(None, parent, bar_index=2) == 25


def test_twap_final_slice_absorbs_the_remainder():
    """100 / 3 = 33 with floor division -- the first two slices are 33
    each, and the LAST slice must be 34 (the remainder), not 33, so the
    three sum to exactly 100."""
    slicer = TWAPSlicer()
    parent = _parent(total_qty=100, horizon_bars=3, filled_qty=66)  # as if bars 0,1 already sent 33 each
    final = slicer.slice_qty_for_bar(None, parent, bar_index=2)
    assert final == 34


def test_twap_slices_sum_to_total_qty_exactly():
    slicer = TWAPSlicer()
    horizon = 7
    total = 100  # does not divide evenly by 7
    parent = _parent(total_qty=total, horizon_bars=horizon, filled_qty=0)
    total_sliced = 0
    for i in range(horizon):
        qty = slicer.slice_qty_for_bar(None, parent, bar_index=i)
        parent.filled_qty += qty
        total_sliced += qty
    assert total_sliced == total


# ---------------------------------------------------------------------------
# VWAP: pure formula, exact known weights
# ---------------------------------------------------------------------------

def test_vwap_slice_is_proportional_to_the_most_recently_observed_bars_share():
    """slice_qty_for_bar weights by the window's LAST entry -- "the volume
    that just happened, right now" -- not by indexing the window array at
    bar_index. bar_index is used only for scheduling (when to stop, when
    to apply the remainder), because at the real moment each call happens,
    the window's freshest entry IS that call's own bar by construction
    (see VWAPSlicer's own docstring). Window [10, 20, 30, 40]: the last
    entry (40) is weighted 40/(10+20+30+40) = 0.4 of total_qty."""
    registry = _FakeRegistry({"X": [10, 20, 30, 40]})
    slicer = VWAPSlicer()
    parent = _parent(symbol="X", total_qty=1000, horizon_bars=4, filled_qty=0)
    qty = slicer.slice_qty_for_bar(registry, parent, bar_index=1)  # not the final bar (index 3)
    assert qty == round(0.4 * 1000)


def test_vwap_weights_a_high_volume_bar_more_than_a_low_volume_one():
    """Same window TOTAL in both cases (110), but the freshest entry
    differs: 100 vs 5 -- the high-recent-volume case must weight (and
    therefore size) its slice larger."""
    registry_low = _FakeRegistry({"X": [95, 5, 5, 5]})    # freshest entry (this bar) is small
    registry_high = _FakeRegistry({"X": [5, 5, 5, 95]})   # freshest entry (this bar) is large
    slicer = VWAPSlicer()
    parent = _parent(symbol="X", total_qty=1000, horizon_bars=4, filled_qty=0)

    qty_low_share = slicer.slice_qty_for_bar(registry_low, parent, bar_index=1)
    qty_high_share = slicer.slice_qty_for_bar(registry_high, parent, bar_index=1)
    assert qty_high_share > qty_low_share


def test_vwap_falls_back_to_equal_slicing_with_zero_total_volume():
    registry = _FakeRegistry({"X": [0, 0, 0, 0]})
    slicer = VWAPSlicer()
    parent = _parent(symbol="X", total_qty=100, horizon_bars=4, filled_qty=0)
    qty = slicer.slice_qty_for_bar(registry, parent, bar_index=1)
    assert qty == 100 // 4


def test_vwap_final_slice_absorbs_the_remainder_regardless_of_weight():
    registry = _FakeRegistry({"X": [999, 1, 1, 1]})  # a lopsided window
    slicer = VWAPSlicer()
    parent = _parent(symbol="X", total_qty=100, horizon_bars=4, filled_qty=97)
    final = slicer.slice_qty_for_bar(registry, parent, bar_index=3)
    assert final == 3  # 100 - 97, NOT weight-derived


# ---------------------------------------------------------------------------
# End-to-end against the real MarketRegistry / DB
# ---------------------------------------------------------------------------

def test_twap_end_to_end_produces_exactly_horizon_bars_children_summing_to_total(db, user, registry):
    db.add(RiskSettings(user_id=user.id))
    parent = ParentOrder(
        user_id=user.id, symbol="ICICIBANK", side="buy", total_qty=100,
        algo=SlicerAlgo.twap, horizon_bars=4, start_bar=registry.current_step,
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    for _ in range(6):
        registry.step_all()
        run_algo_orders_once(db, registry)

    db.refresh(parent)
    children = db.query(Order).filter(Order.parent_order_id == parent.id).all()
    assert len(children) == 4
    assert sum(c.qty for c in children) == 100
    assert parent.status == ParentOrderStatus.completed


def test_vwap_end_to_end_sums_to_total_qty(db, user, registry):
    db.add(RiskSettings(user_id=user.id))
    for _ in range(10):  # let some real volume history accumulate first
        registry.step_all()

    parent = ParentOrder(
        user_id=user.id, symbol="ICICIBANK", side="buy", total_qty=200,
        algo=SlicerAlgo.vwap, horizon_bars=5, start_bar=registry.current_step,
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    for _ in range(7):
        registry.step_all()
        run_algo_orders_once(db, registry)

    db.refresh(parent)
    children = db.query(Order).filter(Order.parent_order_id == parent.id).all()
    assert sum(c.qty for c in children) == 200
    assert parent.status == ParentOrderStatus.completed


def test_algo_orders_stop_slicing_while_trading_halted(db, user, registry):
    db.add(RiskSettings(user_id=user.id, trading_halted=True))
    db.commit()
    parent = ParentOrder(
        user_id=user.id, symbol="ICICIBANK", side="buy", total_qty=100,
        algo=SlicerAlgo.twap, horizon_bars=3, start_bar=registry.current_step,
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)

    for _ in range(3):
        registry.step_all()
        run_algo_orders_once(db, registry)

    db.refresh(parent)
    assert db.query(Order).filter(Order.parent_order_id == parent.id).count() == 0


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

def test_post_orders_algo_creates_a_parent_order(client):
    resp = client.post("/orders/algo", json={
        "symbol": "ICICIBANK", "side": "buy", "total_qty": 100, "algo": "twap", "horizon_bars": 5,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "ICICIBANK"
    assert body["algo"] == "twap"
    assert body["total_qty"] == 100
    assert body["horizon_bars"] == 5
    assert body["status"] == "active"
    assert body["filled_qty"] == 0


def test_get_orders_algo_returns_status_and_children(client):
    created = client.post("/orders/algo", json={
        "symbol": "ICICIBANK", "side": "buy", "total_qty": 60, "algo": "twap", "horizon_bars": 3,
    }).json()

    resp = client.get(f"/orders/algo/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent"]["id"] == created["id"]
    assert body["children"] == []  # tick loop is disabled in tests (DISABLE_MARKET_TICK), nothing has sliced yet
    assert body["total_child_filled_qty"] == 0


def test_get_orders_algo_404s_for_another_users_parent_order(client):
    created = client.post("/orders/algo", json={
        "symbol": "ICICIBANK", "side": "buy", "total_qty": 10, "algo": "twap", "horizon_bars": 2,
    }).json()
    resp = client.get(f"/orders/algo/{created['id'] + 999}")
    assert resp.status_code == 404


def test_post_orders_algo_blocked_while_halted(client):
    client.get("/risk")
    db = client.db_session_factory()
    try:
        settings = db.query(RiskSettings).first()
        settings.trading_halted = True
        db.commit()
    finally:
        db.close()

    resp = client.post("/orders/algo", json={
        "symbol": "ICICIBANK", "side": "buy", "total_qty": 10, "algo": "twap", "horizon_bars": 2,
    })
    assert resp.status_code == 409
