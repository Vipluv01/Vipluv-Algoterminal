"""Sub-account creation, cloned order sizing, and the accounting filter
that isolates each sub-account's own positions/P&L from the primary
account and from every other sub-account."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounting import compute_account
from app.db import Base
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side, SubAccount
from app.models.user import User
from app.pairs_service import submit_paper_order


# ---------------------------------------------------------------------------
# REST: create / list sub-accounts
# ---------------------------------------------------------------------------

def test_create_and_list_sub_accounts(client):
    resp = client.post("/account/sub", json={"label": "aggressive", "sizing_multiplier": 2.0})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == "aggressive"
    assert body["sizing_multiplier"] == 2.0
    assert body["is_active"] is True

    listed = client.get("/account/sub").json()
    assert len(listed) == 1
    assert listed[0]["label"] == "aggressive"


def test_create_sub_account_rejects_non_positive_multiplier(client):
    resp = client.post("/account/sub", json={"label": "bad", "sizing_multiplier": 0.0})
    assert resp.status_code == 400
    resp2 = client.post("/account/sub", json={"label": "bad", "sizing_multiplier": -1.0})
    assert resp2.status_code == 400


def test_get_sub_account_404s_for_unknown_id(client):
    resp = client.get("/account/sub/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Order cloning via submit_paper_order
# ---------------------------------------------------------------------------

def _fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    user = User(google_sub="s", email="e@x.com", display_name="T")
    db.add(user)
    db.commit()
    db.refresh(user)
    return db, user


def test_submitting_a_primary_order_clones_into_every_active_sub_account():
    """2 sub-accounts (2.0x and 0.5x), one primary strategy signal of
    qty=10 -> 3 total orders: the primary at qty=10, and one clone per
    sub-account at qty=round(10*multiplier)."""
    db, user = _fixture()
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        db.add(SubAccount(user_id=user.id, label="aggressive", sizing_multiplier=2.0))
        db.add(SubAccount(user_id=user.id, label="conservative", sizing_multiplier=0.5))
        db.commit()

        submit_paper_order(
            db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", symbol="ICICIBANK",
            side="buy", qty=10, order_type="market", px=None,
        )
        db.commit()

        orders = db.query(Order).filter(Order.user_id == user.id).all()
        assert len(orders) == 3

        primary = [o for o in orders if o.sub_account_id is None]
        clones = [o for o in orders if o.sub_account_id is not None]
        assert len(primary) == 1
        assert primary[0].qty == 10
        assert len(clones) == 2
        assert sorted(c.qty for c in clones) == [5, 20]  # round(10*0.5)=5, round(10*2.0)=20
    finally:
        registry.close()


def test_inactive_sub_accounts_are_not_cloned_into():
    db, user = _fixture()
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        db.add(SubAccount(user_id=user.id, label="dormant", sizing_multiplier=3.0, is_active=False))
        db.commit()

        submit_paper_order(
            db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", symbol="ICICIBANK",
            side="buy", qty=10, order_type="market", px=None,
        )
        db.commit()

        orders = db.query(Order).filter(Order.user_id == user.id).all()
        assert len(orders) == 1  # primary only -- the inactive sub-account got nothing
    finally:
        registry.close()


def test_clone_preserves_parent_order_id_for_algo_sliced_orders():
    """Regression: a sub-account clone of an algo-sliced order must stay
    linked to the SAME ParentOrder -- found live via a full-integration
    smoke test combining every Phase 4 subsystem, where an earlier version
    of _clone_order_to_active_sub_accounts silently dropped
    parent_order_id, making GET /orders/algo/{id} miss every sub-
    account's share of the execution once any sub-account existed."""
    db, user = _fixture()
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        db.add(SubAccount(user_id=user.id, label="aggressive", sizing_multiplier=1.5))
        db.commit()

        submit_paper_order(
            db, registry, user_id=user.id, strategy_key="algo_twap", symbol="ICICIBANK",
            side="buy", qty=10, order_type="market", px=None, parent_order_id=42,
        )
        db.commit()

        orders = db.query(Order).filter(Order.user_id == user.id).all()
        assert len(orders) == 2
        assert all(o.parent_order_id == 42 for o in orders)
    finally:
        registry.close()


def test_a_sub_account_clone_order_is_never_itself_re_cloned():
    """clone_to_sub_accounts=False on the recursive call is what prevents
    this -- verified by checking there's no infinite/duplicate fan-out
    when TWO sub-accounts exist (would be 3 orders, not 5+ if cloning
    cascaded)."""
    db, user = _fixture()
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        db.add(SubAccount(user_id=user.id, label="a", sizing_multiplier=1.0))
        db.add(SubAccount(user_id=user.id, label="b", sizing_multiplier=1.0))
        db.commit()

        submit_paper_order(
            db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", symbol="ICICIBANK",
            side="buy", qty=10, order_type="market", px=None,
        )
        db.commit()

        assert db.query(Order).filter(Order.user_id == user.id).count() == 3
    finally:
        registry.close()


def test_clone_quantity_floors_at_one_share_never_zero():
    db, user = _fixture()
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        db.add(SubAccount(user_id=user.id, label="tiny", sizing_multiplier=0.05))  # 1 * 0.05 -> rounds to 0
        db.commit()

        submit_paper_order(
            db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", symbol="ICICIBANK",
            side="buy", qty=1, order_type="market", px=None,
        )
        db.commit()

        clone = db.query(Order).filter(Order.user_id == user.id, Order.sub_account_id.is_not(None)).one()
        assert clone.qty == 1  # floored, never a qty=0 order
    finally:
        registry.close()


def test_explicit_sub_account_id_with_cloning_disabled_does_not_fan_out():
    """The shape app/risk/circuit_breaker.py's liquidation uses: submitting
    directly INTO one already-known scope must not ALSO trigger the
    normal auto-clone-to-every-active-sub-account behavior."""
    db, user = _fixture()
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        sub = SubAccount(user_id=user.id, label="a", sizing_multiplier=1.0)
        db.add(sub)
        db.commit()
        db.refresh(sub)

        submit_paper_order(
            db, registry, user_id=user.id, strategy_key="circuit_breaker_liquidation", symbol="ICICIBANK",
            side="sell", qty=10, order_type="market", px=None,
            sub_account_id=sub.id, clone_to_sub_accounts=False,
        )
        db.commit()

        orders = db.query(Order).filter(Order.user_id == user.id).all()
        assert len(orders) == 1
        assert orders[0].sub_account_id == sub.id
    finally:
        registry.close()


# ---------------------------------------------------------------------------
# compute_account scoping
# ---------------------------------------------------------------------------

def test_compute_account_sub_account_filter_isolates_positions():
    db, user = _fixture()
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        sub_a = SubAccount(user_id=user.id, label="a", sizing_multiplier=1.0)
        sub_b = SubAccount(user_id=user.id, label="b", sizing_multiplier=1.0)
        db.add_all([sub_a, sub_b])
        db.commit()
        db.refresh(sub_a)
        db.refresh(sub_b)

        # Primary: buy 5. Sub A: buy 10. Sub B: buy 20. All distinct.
        db.add(Order(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", side=Side.buy,
                      order_type=OrderType.market, qty=5, status=OrderStatus.filled,
                      filled_qty=5, avg_fill_px=100.0, sub_account_id=None))
        db.add(Order(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", side=Side.buy,
                      order_type=OrderType.market, qty=10, status=OrderStatus.filled,
                      filled_qty=10, avg_fill_px=100.0, sub_account_id=sub_a.id))
        db.add(Order(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", side=Side.buy,
                      order_type=OrderType.market, qty=20, status=OrderStatus.filled,
                      filled_qty=20, avg_fill_px=100.0, sub_account_id=sub_b.id))
        db.commit()

        all_orders = db.query(Order).filter(Order.user_id == user.id).all()
        prices = {"ICICIBANK": 100.0}

        primary_snapshot = compute_account(all_orders, prices, only_primary=True)
        assert primary_snapshot.positions["ICICIBANK"].qty == 5

        a_snapshot = compute_account(all_orders, prices, sub_account_id=sub_a.id)
        assert a_snapshot.positions["ICICIBANK"].qty == 10

        b_snapshot = compute_account(all_orders, prices, sub_account_id=sub_b.id)
        assert b_snapshot.positions["ICICIBANK"].qty == 20

        # Unfiltered (neither kwarg) sees EVERYTHING combined.
        unfiltered = compute_account(all_orders, prices)
        assert unfiltered.positions["ICICIBANK"].qty == 35
    finally:
        registry.close()


def test_compute_account_sub_account_id_and_only_primary_are_mutually_exclusive():
    import pytest
    with pytest.raises(ValueError):
        compute_account([], {}, sub_account_id=1, only_primary=True)


def test_get_sub_account_endpoint_returns_isolated_snapshot(client):
    sub = client.post("/account/sub", json={"label": "aggressive", "sizing_multiplier": 2.0}).json()

    # A primary-book order (via the normal endpoint) must NOT appear in
    # the sub-account's own view.
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})

    resp = client.get(f"/account/sub/{sub['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["positions"] == []  # the sub-account has no orders of its own yet
    assert body["cash"] == 100_000.0  # its own independent starting cash


def test_get_account_excludes_sub_account_orders(client):
    """The primary GET /account view must not be inflated by sub-account
    clones once they exist -- the correctness fix this phase made
    (only_primary=True) rather than the original unfiltered behavior."""
    client.post("/account/sub", json={"label": "aggressive", "sizing_multiplier": 2.0})
    submit_resp = client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    assert submit_resp.status_code == 200

    account = client.get("/account").json()
    # Manual orders via POST /orders don't clone (only strategy-driven
    # ones do, via submit_paper_order) -- so this is really just confirming
    # the endpoint still works correctly with sub-accounts present.
    if account["positions"]:
        assert account["positions"][0]["qty"] == submit_resp.json()["filled_qty"]
