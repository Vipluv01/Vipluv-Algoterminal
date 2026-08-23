from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.strategy_runner as sr
from app.db import Base
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side, StrategyAllocation
from app.models.user import User
from app.strategies.base import MarketSnapshot, Signal


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
    reg = MarketRegistry(symbols={"ICICIBANK": 1250.0, "HDFCBANK": 1650.0}, seed=0)
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


class _AlwaysBuyStrategy:
    key = "fake_always_buy"
    name = "Fake (always buys)"

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        return Signal("buy", 3, "market", None, "test: always buys")


class _NeverSignalsStrategy:
    key = "fake_never"
    name = "Fake (never signals)"

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        return None


def test_no_enabled_allocations_produces_no_orders(db, registry, user):
    sr.run_strategies_once(db, registry)
    assert db.query(Order).count() == 0


def test_disabled_allocation_is_not_evaluated(db, registry, user, monkeypatch):
    monkeypatch.setitem(sr.SINGLE_INSTRUMENT_STRATEGIES, "alpha_rsi_ema", _AlwaysBuyStrategy())
    db.add(StrategyAllocation(user_id=user.id, strategy_key="alpha_rsi_ema", mode=Mode.paper,
                               enabled=False, symbol="ICICIBANK"))
    db.commit()
    sr.run_strategies_once(db, registry)
    assert db.query(Order).count() == 0


def test_live_mode_allocation_is_not_evaluated_by_the_paper_runner(db, registry, user, monkeypatch):
    monkeypatch.setitem(sr.SINGLE_INSTRUMENT_STRATEGIES, "alpha_rsi_ema", _AlwaysBuyStrategy())
    db.add(StrategyAllocation(user_id=user.id, strategy_key="alpha_rsi_ema", mode=Mode.live,
                               enabled=True, symbol="ICICIBANK"))
    db.commit()
    sr.run_strategies_once(db, registry)
    assert db.query(Order).count() == 0


def test_enabled_single_instrument_allocation_without_a_symbol_is_skipped(db, registry, user, monkeypatch):
    monkeypatch.setitem(sr.SINGLE_INSTRUMENT_STRATEGIES, "alpha_rsi_ema", _AlwaysBuyStrategy())
    db.add(StrategyAllocation(user_id=user.id, strategy_key="alpha_rsi_ema", mode=Mode.paper,
                               enabled=True, symbol=None))
    db.commit()
    sr.run_strategies_once(db, registry)
    assert db.query(Order).count() == 0


def test_enabled_single_instrument_strategy_that_signals_creates_an_order(db, registry, user, monkeypatch):
    monkeypatch.setitem(sr.SINGLE_INSTRUMENT_STRATEGIES, "alpha_rsi_ema", _AlwaysBuyStrategy())
    db.add(StrategyAllocation(user_id=user.id, strategy_key="alpha_rsi_ema", mode=Mode.paper,
                               enabled=True, symbol="ICICIBANK"))
    db.commit()

    sr.run_strategies_once(db, registry)

    orders = db.query(Order).all()
    assert len(orders) == 1
    o = orders[0]
    assert o.user_id == user.id
    assert o.strategy_key == "alpha_rsi_ema"
    assert o.symbol == "ICICIBANK"
    assert o.side == Side.buy
    assert o.qty == 3
    assert o.status in (OrderStatus.filled, OrderStatus.partially_filled, OrderStatus.submitted)


def test_strategy_that_never_signals_creates_no_orders(db, registry, user, monkeypatch):
    monkeypatch.setitem(sr.SINGLE_INSTRUMENT_STRATEGIES, "momentum_macd", _NeverSignalsStrategy())
    db.add(StrategyAllocation(user_id=user.id, strategy_key="momentum_macd", mode=Mode.paper,
                               enabled=True, symbol="ICICIBANK"))
    db.commit()
    sr.run_strategies_once(db, registry)
    assert db.query(Order).count() == 0


def test_unrecognized_strategy_key_is_skipped_not_raised(db, registry, user):
    db.add(StrategyAllocation(user_id=user.id, strategy_key="totally_made_up", mode=Mode.paper,
                               enabled=True, symbol="ICICIBANK"))
    db.commit()
    sr.run_strategies_once(db, registry)  # must not raise
    assert db.query(Order).count() == 0


def test_current_pair_position_reads_none_with_no_orders(db, user):
    assert sr._current_pair_position(db, user.id) == "none"


def test_current_pair_position_reads_long_after_a_filled_buy_on_symbol_a(db, user):
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=sr.PAIRS_STRATEGY_KEY,
        symbol=sr.PAIRS_SYMBOL_A, side=Side.buy, order_type=OrderType.market, qty=10,
        status=OrderStatus.filled, filled_qty=10, avg_fill_px=1250.0,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    assert sr._current_pair_position(db, user.id) == "long_spread"


def test_current_pair_position_reads_short_after_a_filled_sell_on_symbol_a(db, user):
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=sr.PAIRS_STRATEGY_KEY,
        symbol=sr.PAIRS_SYMBOL_A, side=Side.sell, order_type=OrderType.market, qty=10,
        status=OrderStatus.filled, filled_qty=10, avg_fill_px=1250.0,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    assert sr._current_pair_position(db, user.id) == "short_spread"


def test_current_pair_position_ignores_orders_from_a_different_strategy(db, user):
    """A manual trade or a different strategy's fill on the same symbol
    must not be mistaken for this strategy's own open spread position."""
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=None,  # manual trade
        symbol=sr.PAIRS_SYMBOL_A, side=Side.buy, order_type=OrderType.market, qty=10,
        status=OrderStatus.filled, filled_qty=10, avg_fill_px=1250.0,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    assert sr._current_pair_position(db, user.id) == "none"
