from datetime import datetime, timezone

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.strategy_runner as sr
from app.db import Base
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side, StrategyAllocation
from app.models.user import User
from app.pairs_service import current_pair_position
from app.strategies.base import MarketSnapshot, Signal


def _cointegrated_pair(n=300, seed=0, spread_amplitude=0.0):
    """Same construction as test_pairs_cointegration.py's own helper: B is
    a random walk, A = B + constant + small noise, so their spread is
    genuinely mean-reverting (truly cointegrated), not just correlated-
    looking. Kept as a local copy rather than importing across test files,
    matching this test suite's existing per-file-helper convention."""
    rng = np.random.default_rng(seed)
    b = 100 + np.cumsum(rng.normal(0, 0.5, n))
    noise = rng.normal(0, 0.3, n)
    a = b + 5.0 + noise
    if spread_amplitude:
        a = a.copy()
        a[-1] += spread_amplitude
    return a, b


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
    # NOT 3 (the fake strategy's own hardcoded Signal.qty): single-
    # instrument order sizing is now real (see test_kelly_sizing.py) and
    # deliberately overrides whatever qty the strategy's own signal
    # carried. This allocation never set an explicit weight (defaults to
    # 0.0), so sizing floors to the minimum tradeable size.
    assert o.qty == 1
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
    assert current_pair_position(db, user.id) == "none"


def test_current_pair_position_reads_long_after_a_filled_buy_on_symbol_a(db, user):
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=sr.PAIRS_STRATEGY_KEY,
        symbol=sr.PAIRS_SYMBOL_A, side=Side.buy, order_type=OrderType.market, qty=10,
        status=OrderStatus.filled, filled_qty=10, avg_fill_px=1250.0,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    assert current_pair_position(db, user.id) == "long_spread"


def test_current_pair_position_reads_short_after_a_filled_sell_on_symbol_a(db, user):
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=sr.PAIRS_STRATEGY_KEY,
        symbol=sr.PAIRS_SYMBOL_A, side=Side.sell, order_type=OrderType.market, qty=10,
        status=OrderStatus.filled, filled_qty=10, avg_fill_px=1250.0,
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    assert current_pair_position(db, user.id) == "short_spread"


def test_pairs_entry_stamps_entry_zscore_on_both_legs(db, registry, user, monkeypatch):
    a, b = _cointegrated_pair(spread_amplitude=6.0)  # push the last spread value far out -> guaranteed entry
    monkeypatch.setattr(registry, "prices", lambda symbol: a if symbol == sr.PAIRS_SYMBOL_A else b)
    db.add(StrategyAllocation(user_id=user.id, strategy_key=sr.PAIRS_STRATEGY_KEY, mode=Mode.paper, enabled=True))
    db.commit()

    sr.run_strategies_once(db, registry)

    orders = db.query(Order).filter(Order.strategy_key == sr.PAIRS_STRATEGY_KEY).all()
    assert len(orders) == 2
    order_a = next(o for o in orders if o.symbol == sr.PAIRS_SYMBOL_A)
    order_b = next(o for o in orders if o.symbol == sr.PAIRS_SYMBOL_B)
    assert order_a.entry_zscore is not None
    assert order_b.entry_zscore is not None
    assert order_a.entry_zscore == pytest.approx(order_b.entry_zscore)


def test_pairs_close_leaves_entry_zscore_null(db, registry, user, monkeypatch):
    """A closing order isn't opening anything -- entry_zscore must stay
    None for it, even though PairSignal.zscore is a real, non-null value at
    close time too (see strategy_runner._run_pairs: entry_zscore is only
    set when new_position != 'none')."""
    a, b = _cointegrated_pair(spread_amplitude=20.0)  # push z far past stop_z
    monkeypatch.setattr(registry, "prices", lambda symbol: a if symbol == sr.PAIRS_SYMBOL_A else b)
    db.add(Order(
        user_id=user.id, mode=Mode.paper, strategy_key=sr.PAIRS_STRATEGY_KEY,
        symbol=sr.PAIRS_SYMBOL_A, side=Side.sell, order_type=OrderType.market, qty=10,
        status=OrderStatus.filled, filled_qty=10, avg_fill_px=100.0, entry_zscore=-2.0,
        created_at=datetime.now(timezone.utc),
    ))
    db.add(StrategyAllocation(user_id=user.id, strategy_key=sr.PAIRS_STRATEGY_KEY, mode=Mode.paper, enabled=True))
    db.commit()
    assert current_pair_position(db, user.id) == "short_spread"

    sr.run_strategies_once(db, registry)

    closing_orders = (
        db.query(Order)
        .filter(Order.strategy_key == sr.PAIRS_STRATEGY_KEY, Order.entry_zscore.is_(None))
        .all()
    )
    assert len(closing_orders) == 2  # the stop-loss close, both legs


def test_current_pair_position_updates_incrementally_across_multiple_calls(db, user):
    """compute_pair_position_state (app/pairs_service.py) is now an
    incremental, cached walk -- same latency fix as accounting.
    get_cached_account_snapshot, since this function is called from the
    tick loop every second AND from routers/pairs.py's read-only pages,
    polled every 5s. This drives it across three separate calls with new
    orders inserted between each, confirming a repeat call correctly
    folds in only what's new rather than either double-counting or
    silently dropping fills that arrived after the first call cached
    something."""
    def _fill(side, qty, minute):
        db.add(Order(
            user_id=user.id, mode=Mode.paper, strategy_key=sr.PAIRS_STRATEGY_KEY,
            symbol=sr.PAIRS_SYMBOL_A, side=side, order_type=OrderType.market, qty=qty,
            status=OrderStatus.filled, filled_qty=qty, avg_fill_px=1250.0,
            created_at=datetime(2026, 1, 1, 10, minute, tzinfo=timezone.utc),
        ))
        db.commit()

    _fill(Side.buy, 10, 0)
    state1 = current_pair_position(db, user.id)
    assert state1 == "long_spread"

    _fill(Side.buy, 5, 1)  # adds -> still long, now 15
    state2 = current_pair_position(db, user.id)
    assert state2 == "long_spread"

    _fill(Side.sell, 20, 2)  # flips through flat -> short 5
    state3 = current_pair_position(db, user.id)
    assert state3 == "short_spread"

    # Cross-check against a from-scratch computation over ALL orders --
    # the incremental cache must agree with what a full walk would say.
    from app.pairs_service import PairPositionState, compute_pair_position_state
    all_orders = (
        db.query(Order)
        .filter(Order.user_id == user.id, Order.strategy_key == sr.PAIRS_STRATEGY_KEY, Order.mode == Mode.paper)
        .all()
    )
    net_a = sum((o.filled_qty if o.side == Side.buy else -o.filled_qty) for o in all_orders if o.symbol == sr.PAIRS_SYMBOL_A)
    expected = PairPositionState(position="short_spread", qty_a=abs(net_a))
    assert compute_pair_position_state(db, user.id) == expected


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
    assert current_pair_position(db, user.id) == "none"
