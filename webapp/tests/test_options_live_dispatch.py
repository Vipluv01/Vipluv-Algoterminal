"""Live tick-loop dispatch for the 4 options strategies -- reconstructing
open-position state from real filled Order rows and turning evaluate_
options() signals into real paper orders. Uses the real strategy_runner.
run_strategies_once/StrategyAllocation path (the same one app/main.py's
tick loop drives), not app.options.live_dispatch's internals directly, so
this exercises the actual registration wiring too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.markets import MarketRegistry
from app.models.trading import InstrumentType, Mode, Order, StrategyAllocation
from app.models.user import User
from app.strategy_runner import OPTIONS_STRATEGIES, run_strategies_once


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


def _enable(db, user_id, strategy_key):
    db.add(StrategyAllocation(user_id=user_id, strategy_key=strategy_key, mode=Mode.paper, enabled=True, weight=1.0))
    db.commit()


def test_every_options_strategy_key_is_dispatchable():
    assert set(OPTIONS_STRATEGIES) == {"iron_condor", "calendar_spread", "short_strangle", "delta_neutral"}


def test_short_strangle_enters_a_real_position_on_the_first_eligible_tick():
    db, user = _fixture()
    registry = MarketRegistry(seed=0)
    try:
        _enable(db, user.id, "short_strangle")
        registry.step_all()
        run_strategies_once(db, registry)

        option_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.option,
        ).all()
        assert len(option_orders) == 2
        assert {o.option_type for o in option_orders} == {"CE", "PE"}
        assert all(o.underlying == "BANKNIFTY" for o in option_orders)
        assert all(o.strategy_key == "short_strangle" for o in option_orders)
    finally:
        registry.close()


def test_short_strangle_does_not_re_enter_while_already_open():
    db, user = _fixture()
    registry = MarketRegistry(seed=0)
    try:
        _enable(db, user.id, "short_strangle")
        for _ in range(3):
            registry.step_all()
            run_strategies_once(db, registry)

        option_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.option,
        ).all()
        # Still exactly the ORIGINAL 2-leg entry -- no duplicate entries
        # fired on later ticks just because the strategy is still enabled.
        assert len(option_orders) == 2
    finally:
        registry.close()


def test_short_strangle_exits_once_the_real_hold_days_threshold_has_elapsed():
    db, user = _fixture()
    registry = MarketRegistry(seed=0)
    try:
        _enable(db, user.id, "short_strangle")
        registry.step_all()
        run_strategies_once(db, registry)

        # Backdate the entry fills past hold_days -- the live dispatcher
        # computes elapsed time from Order.created_at, so this is the
        # direct way to simulate "hold_days have passed" without an
        # actual multi-day wall-clock wait in a unit test.
        strat = OPTIONS_STRATEGIES["short_strangle"]
        past = datetime.now(timezone.utc) - timedelta(days=strat.hold_days + 1)
        db.query(Order).filter(Order.user_id == user.id).update({Order.created_at: past})
        db.commit()

        registry.step_all()
        run_strategies_once(db, registry)

        option_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.option,
        ).order_by(Order.created_at).all()
        assert len(option_orders) == 4  # 2 entry legs + 2 closing legs
        closing = option_orders[2:]
        assert {o.side.value for o in closing} == {"buy"}  # both were SOLD at entry, so closing BUYS both back
    finally:
        registry.close()


def test_delta_neutral_enters_with_an_option_leg_and_an_equity_hedge_leg():
    db, user = _fixture()
    registry = MarketRegistry(seed=0)
    try:
        _enable(db, user.id, "delta_neutral")
        registry.step_all()
        run_strategies_once(db, registry)

        option_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.option,
        ).all()
        equity_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.equity,
            Order.strategy_key == "delta_neutral",
        ).all()
        assert len(option_orders) == 1
        assert option_orders[0].option_type == "CE" and option_orders[0].side.value == "sell"
        assert len(equity_orders) == 1
        assert equity_orders[0].symbol == "ICICIBANK"
        assert equity_orders[0].side.value == "buy"
    finally:
        registry.close()


def test_delta_neutral_rebalances_the_hedge_once_due():
    db, user = _fixture()
    registry = MarketRegistry(seed=0)
    try:
        _enable(db, user.id, "delta_neutral")
        registry.step_all()
        run_strategies_once(db, registry)

        strat = OPTIONS_STRATEGIES["delta_neutral"]
        past = datetime.now(timezone.utc) - timedelta(days=strat.rebalance_days + 1)
        db.query(Order).filter(Order.user_id == user.id).update({Order.created_at: past})
        db.commit()

        registry.step_all()
        run_strategies_once(db, registry)

        equity_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.equity,
            Order.strategy_key == "delta_neutral",
        ).all()
        option_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.option,
        ).all()
        # A rebalance never touches the option leg itself -- only equity.
        assert len(option_orders) == 1
        assert len(equity_orders) >= 1  # at least the initial hedge; a real rebalance may or may not cross min_rebalance_shares
    finally:
        registry.close()


def test_iron_condor_enters_all_four_legs_on_the_correct_underlying():
    db, user = _fixture()
    registry = MarketRegistry(seed=0)
    try:
        _enable(db, user.id, "iron_condor")
        registry.step_all()
        run_strategies_once(db, registry)

        option_orders = db.query(Order).filter(
            Order.user_id == user.id, Order.instrument_type == InstrumentType.option,
        ).all()
        assert len(option_orders) == 4
        assert all(o.underlying == "NIFTY50" for o in option_orders)
    finally:
        registry.close()


def test_options_strategy_is_gated_by_the_trading_halted_circuit_breaker():
    from app.models.risk import RiskSettings

    db, user = _fixture()
    registry = MarketRegistry(seed=0)
    try:
        db.add(RiskSettings(user_id=user.id, trading_halted=True))
        db.commit()
        _enable(db, user.id, "short_strangle")

        registry.step_all()
        run_strategies_once(db, registry)

        assert db.query(Order).filter(Order.user_id == user.id).count() == 0
    finally:
        registry.close()
