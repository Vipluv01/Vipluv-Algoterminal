"""API-level tests for GET /optimizer.

The "enough real history" path needs >=5 distinct trading days of realized
P&L, which the client fixture's dev user can't produce through real HTTP
calls within a single test (every order lands on today's date -- there's
no way to backdate created_at through the API, by design: it's a real
system field, not a test hook). For that path, this calls the router
function directly against a manually-seeded DB session with backdated
orders, then runs the result through FastAPI's own jsonable_encoder --
the exact mechanism that caught the numpy.bool_ leak in app/routers/pairs.py
-- rather than trusting `.tolist()`/`float()` calls by inspection alone.
"""

from datetime import datetime, timezone

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User
from app.routers.optimizer import STRATEGY_KEYS, get_optimizer


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
    u = User(google_sub="test-sub", email="t@example.com", display_name="Test")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _day(n):
    return datetime(2026, 8, n, 10, 0, tzinfo=timezone.utc)


def _order(user_id, symbol, side, qty, px, strategy_key, day):
    return Order(
        user_id=user_id, mode=Mode.paper, strategy_key=strategy_key, symbol=symbol,
        side=side, order_type=OrderType.market, qty=qty, px=None, status=OrderStatus.filled,
        filled_qty=qty, avg_fill_px=px, created_at=day,
    )


def test_overview_reports_insufficient_history_via_real_http(client):
    resp = client.get("/optimizer")
    assert resp.status_code == 200
    body = resp.json()
    assert body["insufficient_history"] is True
    assert set(body["strategy_keys"]) == set(STRATEGY_KEYS)


def test_optimizer_result_serializes_cleanly_with_enough_real_history(db, user):
    a, b = STRATEGY_KEYS[0], STRATEGY_KEYS[1]
    orders = []
    for d in range(1, 8):
        orders.append(_order(user.id, "X", Side.buy, 10, 100.0, a, _day(d)))
        orders.append(_order(user.id, "X", Side.sell, 10, 100.0 + d, a, _day(d)))
        orders.append(_order(user.id, "Y", Side.buy, 10, 200.0, b, _day(d)))
        orders.append(_order(user.id, "Y", Side.sell, 10, 200.0 - d, b, _day(d)))
    db.add_all(orders)
    db.commit()

    result = get_optimizer(user=user, db=db)

    assert result["insufficient_history"] is False
    assert set(result["strategy_keys"]) == {a, b}
    assert result["days_of_history"] == 7
    assert abs(sum(result["weights"]) - 1.0) < 1e-9

    # The actual regression check: this must not raise, the same way it
    # crashed live in app/routers/pairs.py before that fix.
    encoded = jsonable_encoder(result)
    assert encoded["insufficient_history"] is False
