"""Wiring Kelly sizing into live single-instrument execution -- see
strategy_runner._size_single_instrument_qty. Scoped to the 5 single-
instrument strategies deliberately: pairs/basket strategies already do
their own internal sizing (pairs_kelly's self-contained Kelly scan,
pairs_cointegration/multi_basket's leg-proportional fixed sizing), and a
runner-level override would either double-apply Kelly (pairs_kelly) or
break leg-quantity proportionality (the others) -- see strategy_runner.
_run_single_instrument's own comment on this scope boundary.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.strategy_runner as sr
from app.db import Base
from app.markets import MarketRegistry
from app.models.risk import RiskSettings
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


class _AlwaysBuyStrategy:
    key = "fake_always_buy"
    name = "Fake (always buys)"

    def evaluate(self, market: MarketSnapshot) -> Signal | None:
        return Signal("buy", 3, "market", None, "test: always buys")  # qty=3 must be OVERRIDDEN by real sizing


def _historical_trade(db, user, *, strategy_key, symbol, entry_side, exit_side, entry_px, exit_px, qty, day_offset):
    """One resolved round trip: an entry fill followed by a fully-closing
    exit fill -- the unit compute_realizations turns into exactly one
    TradeRealization, which dashboard_stats.compute_trade_stats then
    classifies as a win or a loss by its sign."""
    base = datetime.now(timezone.utc)
    entry_time = base.replace(hour=0, minute=0, second=0, microsecond=0)
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=strategy_key, symbol=symbol,
        side=entry_side, order_type=OrderType.market, qty=qty, status=OrderStatus.filled,
        filled_qty=qty, avg_fill_px=entry_px, created_at=entry_time,
    ))
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=strategy_key, symbol=symbol,
        side=exit_side, order_type=OrderType.market, qty=qty, status=OrderStatus.filled,
        filled_qty=qty, avg_fill_px=exit_px, created_at=entry_time.replace(second=1),
    ))
    db.commit()


def _seed_win_loss_history(db, user, *, strategy_key, symbol, wins, losses, entry_px=100.0, qty=1):
    """wins/losses are counts; each win realizes avg_win=100, each loss
    realizes avg_loss=50 -- exactly the figures the hand-calculated test
    below uses, so seeding N of each produces a known win_rate/avg_win/
    avg_loss triple regardless of N."""
    for _ in range(wins):
        _historical_trade(
            db, user, strategy_key=strategy_key, symbol=symbol,
            entry_side=Side.buy, exit_side=Side.sell, entry_px=entry_px, exit_px=entry_px + 100.0,
            qty=qty, day_offset=0,
        )
    for _ in range(losses):
        _historical_trade(
            db, user, strategy_key=strategy_key, symbol=symbol,
            entry_side=Side.buy, exit_side=Side.sell, entry_px=entry_px, exit_px=entry_px - 50.0,
            qty=qty, day_offset=0,
        )


# ---------------------------------------------------------------------------
# Hand-calculated Kelly qty
# ---------------------------------------------------------------------------

def test_matches_hand_calculated_kelly_qty(db, user, registry):
    """win_rate=0.7 (7 wins / 3 losses out of 10), avg_win=100, avg_loss=50,
    kelly_multiplier=0.5.

    f* = p - q/b = 0.7 - 0.3/(100/50) = 0.7 - 0.15 = 0.55
    applied = f* * kelly_multiplier = 0.55 * 0.5 = 0.275
    account_value = equity * weight; qty = floor(account_value * applied / price)

    max_position_fraction is set to 1.0 here specifically so it does NOT
    bind -- this test isolates the Kelly arithmetic itself; the cap has
    its own dedicated test below.
    """
    settings = RiskSettings(user_id=user.id, kelly_multiplier=0.5, max_position_fraction=1.0, max_order_qty=100_000)
    db.add(settings)
    db.commit()

    _seed_win_loss_history(db, user, strategy_key="alpha_rsi_ema", symbol="ICICIBANK", wins=7, losses=3)

    weight = 1.0
    price = 100.0
    equity = 100_000.0  # starting paper cash, no other orders affecting it here beyond the seeded history's own P&L

    qty = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", weight=weight, price=price,
    )

    # Recompute the expected qty from the SAME equity the function itself
    # would have derived (starting cash + realized P&L from the seeded
    # trades), not a hardcoded 100_000 -- the seeded wins/losses DO move
    # cash, and the test must reflect that real number, not assume it away.
    from app.accounting import compute_account
    all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    real_equity = compute_account(all_orders, registry.current_prices()).total_value

    f_star = 0.7 - 0.3 / (100.0 / 50.0)
    applied = f_star * 0.5
    expected_qty = int((real_equity * weight * applied) // price)

    assert qty == expected_qty
    assert qty > 0


def test_max_position_fraction_caps_the_kelly_result(db, user, registry):
    """The literal formula this phase specifies has no cap term at all --
    this test is what proves max_position_fraction (a real, persisted
    risk setting from THIS phase) actually constrains something, the same
    gap Phase 1 found Kelly itself sitting in."""
    loose = RiskSettings(user_id=user.id, kelly_multiplier=0.5, max_position_fraction=1.0, max_order_qty=100_000)
    db.add(loose)
    db.commit()
    _seed_win_loss_history(db, user, strategy_key="alpha_rsi_ema", symbol="ICICIBANK", wins=7, losses=3)
    uncapped_qty = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", weight=1.0, price=100.0,
    )

    loose.max_position_fraction = 0.05  # much tighter than the 0.275 Kelly would otherwise apply
    db.commit()
    capped_qty = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", weight=1.0, price=100.0,
    )

    assert capped_qty < uncapped_qty


def test_max_order_qty_caps_the_result(db, user, registry):
    settings = RiskSettings(user_id=user.id, kelly_multiplier=0.5, max_position_fraction=1.0, max_order_qty=5)
    db.add(settings)
    db.commit()
    _seed_win_loss_history(db, user, strategy_key="alpha_rsi_ema", symbol="ICICIBANK", wins=7, losses=3)

    qty = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", weight=1.0, price=100.0,
    )
    assert qty == 5


# ---------------------------------------------------------------------------
# Fallback sizing with insufficient history
# ---------------------------------------------------------------------------

def test_falls_back_to_fixed_fraction_sizing_with_zero_historical_trades(db, user, registry):
    settings = RiskSettings(user_id=user.id, max_order_qty=100_000)
    db.add(settings)
    db.commit()

    weight = 1.0
    price = 100.0
    qty = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", weight=weight, price=price,
    )

    # 0 trades -> fallback: 5% of equity * weight / price. Equity here is
    # just the starting paper cash (no orders at all yet).
    expected = int((100_000.0 * weight * 0.05) // price)
    assert qty == expected


def test_falls_back_when_fewer_than_ten_historical_trades_exist(db, user, registry):
    settings = RiskSettings(user_id=user.id, max_order_qty=100_000)
    db.add(settings)
    db.commit()
    # 5 wins, 3 losses = 8 trades -- below MIN_HISTORICAL_TRADES_FOR_KELLY (10).
    _seed_win_loss_history(db, user, strategy_key="alpha_rsi_ema", symbol="ICICIBANK", wins=5, losses=3)

    from app.accounting import compute_account
    all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    real_equity = compute_account(all_orders, registry.current_prices()).total_value

    qty = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", weight=1.0, price=100.0,
    )
    expected = max(1, int((real_equity * 1.0 * 0.05) // 100.0))
    assert qty == expected


def test_falls_back_when_history_is_all_wins_kelly_cannot_estimate_a_loss_ratio(db, user, registry):
    """>=10 trades, but every one is a win -- kelly_fraction needs a
    positive avg_loss too (position_sizing.py raises without one), so
    this must degrade to the fallback, not crash."""
    settings = RiskSettings(user_id=user.id, max_order_qty=100_000)
    db.add(settings)
    db.commit()
    _seed_win_loss_history(db, user, strategy_key="alpha_rsi_ema", symbol="ICICIBANK", wins=12, losses=0)

    qty = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="alpha_rsi_ema", weight=1.0, price=100.0,
    )
    assert qty >= 1  # did not crash, produced a usable size


def test_kelly_sizing_is_per_strategy_not_shared_across_strategies(db, user, registry):
    """A strategy with a strong track record must not inherit sizing
    derived from a DIFFERENT strategy's history on the same symbol --
    each strategy_key's realizations are isolated."""
    settings = RiskSettings(user_id=user.id, kelly_multiplier=0.5, max_position_fraction=1.0, max_order_qty=100_000)
    db.add(settings)
    db.commit()
    _seed_win_loss_history(db, user, strategy_key="alpha_rsi_ema", symbol="ICICIBANK", wins=9, losses=1)
    # momentum_macd has NO history at all -- must use fallback sizing,
    # unaffected by alpha_rsi_ema's strong record on the same symbol.
    qty_no_history = sr._size_single_instrument_qty(
        db, registry, user_id=user.id, strategy_key="momentum_macd", weight=1.0, price=100.0,
    )
    from app.accounting import compute_account
    all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    real_equity = compute_account(all_orders, registry.current_prices()).total_value
    expected_fallback = max(1, int((real_equity * 1.0 * 0.05) // 100.0))
    assert qty_no_history == expected_fallback


# ---------------------------------------------------------------------------
# Halted -> no orders
# ---------------------------------------------------------------------------

def test_no_orders_submitted_while_trading_is_halted(db, user, registry):
    import app.strategy_runner as sr_module
    sr_module.SINGLE_INSTRUMENT_STRATEGIES["alpha_rsi_ema"] = _AlwaysBuyStrategy()
    try:
        db.add(RiskSettings(user_id=user.id, trading_halted=True))
        db.add(StrategyAllocation(user_id=user.id, strategy_key="alpha_rsi_ema", mode=Mode.paper,
                                   enabled=True, symbol="ICICIBANK", weight=1.0))
        db.commit()

        sr.run_strategies_once(db, registry)

        assert db.query(Order).count() == 0
    finally:
        from app.strategies.alpha import AlphaRSIEMAStrategy
        sr_module.SINGLE_INSTRUMENT_STRATEGIES["alpha_rsi_ema"] = AlphaRSIEMAStrategy()


def test_orders_resume_once_unhalted(db, user, registry):
    import app.strategy_runner as sr_module
    sr_module.SINGLE_INSTRUMENT_STRATEGIES["alpha_rsi_ema"] = _AlwaysBuyStrategy()
    try:
        settings = RiskSettings(user_id=user.id, trading_halted=True)
        db.add(settings)
        db.add(StrategyAllocation(user_id=user.id, strategy_key="alpha_rsi_ema", mode=Mode.paper,
                                   enabled=True, symbol="ICICIBANK", weight=1.0))
        db.commit()

        sr.run_strategies_once(db, registry)
        assert db.query(Order).count() == 0

        settings.trading_halted = False
        db.commit()

        sr.run_strategies_once(db, registry)
        assert db.query(Order).count() == 1
    finally:
        from app.strategies.alpha import AlphaRSIEMAStrategy
        sr_module.SINGLE_INSTRUMENT_STRATEGIES["alpha_rsi_ema"] = AlphaRSIEMAStrategy()
