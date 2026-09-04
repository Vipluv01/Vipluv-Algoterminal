from datetime import datetime, timezone

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounting import compute_realizations
from app.db import Base
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side
from app.optimizer_returns import MIN_TRADING_DAYS, build_daily_return_series


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _day(n):
    return datetime(2026, 8, n, 10, 0, tzinfo=timezone.utc)


def _order(user_id, symbol, side, qty, px, strategy_key, day, filled_qty=None):
    return Order(
        user_id=user_id, mode=Mode.paper, strategy_key=strategy_key, symbol=symbol,
        side=side, order_type=OrderType.market, qty=qty, px=None, status=OrderStatus.filled,
        filled_qty=filled_qty if filled_qty is not None else qty, avg_fill_px=px, created_at=day,
    )


def _realizations(orders, starting_cash=None):
    # build_daily_return_series now takes realizations directly (see its
    # own docstring: GET /optimizer feeds it get_cached_realizations'
    # output rather than re-walking orders itself) -- this helper keeps
    # every test below building orders exactly as before and just adds
    # the one extra walk step a real caller already did upstream.
    return compute_realizations(orders) if starting_cash is None else compute_realizations(orders, starting_cash)


def test_returns_none_with_no_orders(db):
    assert build_daily_return_series([], ["a", "b"]) is None


def test_returns_none_with_only_one_active_strategy(db):
    orders = [
        _order(1, "X", Side.buy, 10, 100.0, "a", _day(1)),
        _order(1, "X", Side.sell, 10, 110.0, "a", _day(2)),
    ]
    assert build_daily_return_series(_realizations(orders), ["a", "b"]) is None


def test_returns_none_with_fewer_than_min_trading_days(db):
    assert MIN_TRADING_DAYS == 5
    orders = [
        _order(1, "X", Side.buy, 10, 100.0, "a", _day(1)),
        _order(1, "X", Side.sell, 10, 110.0, "a", _day(2)),
        _order(1, "Y", Side.buy, 10, 200.0, "b", _day(1)),
        _order(1, "Y", Side.sell, 10, 190.0, "b", _day(2)),
    ]
    assert build_daily_return_series(_realizations(orders), ["a", "b"]) is None


def test_builds_aligned_zero_filled_series_across_min_days(db):
    # Strategy "a" realizes on days 1, 3, 5. Strategy "b" realizes on every day 1-5.
    orders = [
        _order(1, "X", Side.buy, 10, 100.0, "a", _day(1)),
        _order(1, "X", Side.sell, 10, 110.0, "a", _day(1)),  # realizes +100 on day 1
        _order(1, "X", Side.buy, 10, 100.0, "a", _day(3)),
        _order(1, "X", Side.sell, 10, 90.0, "a", _day(3)),   # realizes -100 on day 3
        _order(1, "X", Side.buy, 10, 100.0, "a", _day(5)),
        _order(1, "X", Side.sell, 10, 105.0, "a", _day(5)),  # realizes +50 on day 5
    ]
    for d in range(1, 6):
        orders.append(_order(1, "Y", Side.buy, 10, 200.0, "b", _day(d)))
        orders.append(_order(1, "Y", Side.sell, 10, 201.0, "b", _day(d)))  # realizes +10 each day

    series = build_daily_return_series(_realizations(orders, 1000.0), ["a", "b"], starting_cash=1000.0)
    assert series is not None
    assert set(series.keys()) == {"a", "b"}
    assert len(series["a"]) == 5 == len(series["b"])
    np.testing.assert_allclose(series["a"], [100 / 1000, 0.0, -100 / 1000, 0.0, 50 / 1000])
    np.testing.assert_allclose(series["b"], [10 / 1000] * 5)


def test_ignores_realizations_for_strategy_keys_not_in_the_requested_list(db):
    orders = [
        _order(1, "X", Side.buy, 10, 100.0, "a", _day(1)),
        _order(1, "X", Side.sell, 10, 110.0, "a", _day(1)),
        _order(1, "Y", Side.buy, 10, 200.0, "not_requested", _day(1)),
        _order(1, "Y", Side.sell, 10, 190.0, "not_requested", _day(1)),
    ]
    realizations = _realizations(orders)
    # Only "a" is in the requested list -- "not_requested" must not count toward the >=2-active-strategies floor.
    assert build_daily_return_series(realizations, ["a"]) is None
    assert build_daily_return_series(realizations, ["a", "not_requested"]) is None  # still <5 days
