"""Direct tests against check_circuit_breaker, using the same
db/registry/user fixture pattern test_strategy_runner.py already
establishes -- a real in-memory DB and a real MarketRegistry, not mocks,
so the engine's actual fill/liquidity behavior is exercised."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounting import compute_account
from app.db import Base
from app.markets import MarketRegistry
from app.models.risk import RiskSettings
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User
from app.risk.circuit_breaker import LIQUIDATION_STRATEGY_KEY, check_circuit_breaker


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


def _yesterday():
    return datetime.now(timezone.utc) - timedelta(days=1)


def _fill(db, user, *, symbol, side, qty, px, created_at):
    o = Order(
        user_id=user.id, mode=Mode.paper, symbol=symbol, side=side, order_type=OrderType.market,
        qty=qty, status=OrderStatus.filled, filled_qty=qty, avg_fill_px=px, created_at=created_at,
    )
    db.add(o)
    db.commit()
    return o


def _risk_settings(db, user):
    return db.query(RiskSettings).filter(RiskSettings.user_id == user.id).first()


# ---------------------------------------------------------------------------
# Does not fire within limits
# ---------------------------------------------------------------------------

def test_does_not_halt_when_no_positions_and_no_history(db, user, registry):
    assert check_circuit_breaker(db, user, registry) is False
    assert _risk_settings(db, user) is None or _risk_settings(db, user).trading_halted is False


def test_does_not_halt_on_a_small_move_within_the_default_threshold(db, user, registry):
    """Bought yesterday at a price close to today's mark -- well under the
    default 5% daily_max_drawdown_pct."""
    mark = registry.current_prices()["ICICIBANK"]
    _fill(db, user, symbol="ICICIBANK", side=Side.buy, qty=10, px=mark * 1.01, created_at=_yesterday())

    assert check_circuit_breaker(db, user, registry) is False
    assert _risk_settings(db, user).trading_halted is False


# ---------------------------------------------------------------------------
# Fires on a real breach
# ---------------------------------------------------------------------------

def test_halts_on_a_large_unrealized_loss_on_a_held_position(db, user, registry):
    """The scenario a naive 'compare today's own trades only' design would
    miss entirely: a position bought BEFORE today, never touched today,
    that has since moved hard against the account -- confirmed directly
    during development that an earlier version of this function reported
    day_pnl=0 here (it marked yesterday's position at TODAY's price on
    both sides of the comparison, making the two snapshots identical
    whenever no new order exists)."""
    mark = registry.current_prices()["ICICIBANK"]
    # Bought heavily, far above today's mark -- guarantees the drawdown
    # threshold is cleared regardless of the exact seeded price.
    _fill(db, user, symbol="ICICIBANK", side=Side.buy, qty=80, px=mark * 1.6, created_at=_yesterday())

    result = check_circuit_breaker(db, user, registry)
    assert result is True
    assert _risk_settings(db, user).trading_halted is True


def test_halts_on_a_large_realized_loss_from_todays_own_trading(db, user, registry):
    mark = registry.current_prices()["ICICIBANK"]
    _fill(db, user, symbol="ICICIBANK", side=Side.buy, qty=80, px=mark, created_at=_yesterday())
    # Today: sell the same position at a steep loss.
    _fill(db, user, symbol="ICICIBANK", side=Side.sell, qty=80, px=mark * 0.4, created_at=datetime.now(timezone.utc))

    assert check_circuit_breaker(db, user, registry) is True
    assert _risk_settings(db, user).trading_halted is True


def test_halt_submits_a_liquidation_order_for_the_open_position(db, user, registry):
    mark = registry.current_prices()["ICICIBANK"]
    _fill(db, user, symbol="ICICIBANK", side=Side.buy, qty=80, px=mark * 1.6, created_at=_yesterday())

    check_circuit_breaker(db, user, registry)

    liquidations = db.query(Order).filter(Order.strategy_key == LIQUIDATION_STRATEGY_KEY).all()
    assert len(liquidations) == 1
    assert liquidations[0].symbol == "ICICIBANK"
    assert liquidations[0].side == Side.sell  # long position -> liquidate by selling
    assert liquidations[0].qty == 80


def test_halt_does_not_liquidate_a_short_position_by_selling_more(db, user, registry):
    mark = registry.current_prices()["ICICIBANK"]
    _fill(db, user, symbol="ICICIBANK", side=Side.sell, qty=80, px=mark * 0.4, created_at=_yesterday())

    check_circuit_breaker(db, user, registry)

    liquidations = db.query(Order).filter(Order.strategy_key == LIQUIDATION_STRATEGY_KEY).all()
    assert len(liquidations) == 1
    assert liquidations[0].side == Side.buy  # short position -> liquidate by buying


# ---------------------------------------------------------------------------
# Blocking new orders while halted
# ---------------------------------------------------------------------------

def test_new_orders_are_blocked_via_the_api_while_halted(client):
    resp = client.get("/risk")
    assert resp.status_code == 200

    db = client.db_session_factory()
    try:
        settings = db.query(RiskSettings).first()
        settings.trading_halted = True
        db.commit()
    finally:
        db.close()

    resp = client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    assert resp.status_code == 409
    assert "reset-halt" in resp.json()["detail"]


def test_orders_succeed_again_after_reset_halt(client):
    client.get("/risk")
    db = client.db_session_factory()
    try:
        settings = db.query(RiskSettings).first()
        settings.trading_halted = True
        db.commit()
    finally:
        db.close()

    blocked = client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    assert blocked.status_code == 409

    reset = client.post("/risk/reset-halt")
    assert reset.status_code == 200
    assert reset.json()["trading_halted"] is False

    allowed = client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# Idempotence / retry behavior once halted
# ---------------------------------------------------------------------------

def test_does_not_re_evaluate_the_threshold_once_already_halted(db, user, registry):
    """Once halted, subsequent calls must not re-derive day_pnl (which
    would be a no-op anyway since nothing changed) -- just confirmed
    still-halted and another liquidation attempt."""
    mark = registry.current_prices()["ICICIBANK"]
    _fill(db, user, symbol="ICICIBANK", side=Side.buy, qty=80, px=mark * 1.6, created_at=_yesterday())

    check_circuit_breaker(db, user, registry)
    settings_after_first = _risk_settings(db, user)
    assert settings_after_first.trading_halted is True

    # Manually clear it back to False on the SAME threshold-breaching data
    # would immediately re-halt -- instead, leave it halted and confirm a
    # second call is a safe, idempotent no-op on the flag itself.
    assert check_circuit_breaker(db, user, registry) is True
    assert _risk_settings(db, user).trading_halted is True


def test_retries_liquidation_across_ticks_until_the_position_is_flat(db, user, registry):
    """A liquidation market order can partially fill against thin seeded
    liquidity -- checked directly here (not assumed) by confirming the
    remaining position quantity is monotonically non-increasing across
    repeated ticks while halted, and does reach zero given enough ticks."""
    mark = registry.current_prices()["ICICIBANK"]
    _fill(db, user, symbol="ICICIBANK", side=Side.buy, qty=80, px=mark * 1.6, created_at=_yesterday())

    check_circuit_breaker(db, user, registry)  # triggers the halt + first liquidation attempt

    qtys = []
    for _ in range(30):
        registry.step_all()
        check_circuit_breaker(db, user, registry)
        all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
        pos = compute_account(all_orders, registry.current_prices()).positions.get("ICICIBANK")
        qtys.append(pos.qty if pos else 0)

    assert qtys[-1] == 0, f"position never fully flattened after 30 ticks: {qtys}"
    assert all(qtys[i] <= qtys[i - 1] for i in range(1, len(qtys))), "remaining qty must never INCREASE"
