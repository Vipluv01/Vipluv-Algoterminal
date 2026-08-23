import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brackets import cancel_brackets_closed_elsewhere, monitor_brackets
from app.db import Base
from app.markets import MarketRegistry
from app.models.trading import Bracket, BracketStatus, Mode, Order, OrderStatus, OrderType, Side
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


def _entry_order(user_id, symbol="ICICIBANK", side=Side.buy, qty=10, px=1250.0):
    return Order(
        user_id=user_id, mode=Mode.paper, symbol=symbol, side=side, order_type=OrderType.market,
        qty=qty, px=None, status=OrderStatus.filled, filled_qty=qty, avg_fill_px=px,
    )


def test_bracket_not_triggered_stays_active_and_creates_no_closing_order(db, registry, user):
    entry = _entry_order(user.id)
    db.add(entry)
    db.flush()
    # Current market price (~1250) is inside these thresholds -- should not fire.
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.buy, qty=10,
                stop_loss_px=100.0, take_profit_px=5000.0, entry_order_id=entry.id)
    db.add(b)
    db.commit()

    monitor_brackets(db, registry)

    db.refresh(b)
    assert b.status == BracketStatus.active
    assert b.closing_order_id is None
    assert db.query(Order).count() == 1  # only the entry order, no closing order


def test_long_stop_loss_triggers_a_real_closing_sell_and_marks_the_bracket(db, registry, user):
    entry = _entry_order(user.id, side=Side.buy, qty=10, px=1250.0)
    db.add(entry)
    db.flush()
    # Current market price is ~1250 -- set the stop-loss ABOVE it so it fires immediately.
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.buy, qty=10,
                stop_loss_px=5000.0, take_profit_px=None, entry_order_id=entry.id)
    db.add(b)
    db.commit()

    monitor_brackets(db, registry)

    db.refresh(b)
    assert b.status == BracketStatus.triggered
    assert b.closing_order_id is not None
    closing = db.get(Order, b.closing_order_id)
    assert closing.side == Side.sell  # closes a LONG by selling
    assert closing.strategy_key == "bracket_stop_loss"
    assert closing.qty == 10


def test_short_stop_loss_triggers_a_real_closing_buy(db, registry, user):
    entry = _entry_order(user.id, side=Side.sell, qty=10, px=1250.0)
    db.add(entry)
    db.flush()
    # Stop-loss for a SHORT fires as price rises -- set it BELOW current
    # price so it's already past the threshold.
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.sell, qty=10,
                stop_loss_px=1.0, take_profit_px=None, entry_order_id=entry.id)
    db.add(b)
    db.commit()

    monitor_brackets(db, registry)

    db.refresh(b)
    assert b.status == BracketStatus.triggered
    closing = db.get(Order, b.closing_order_id)
    assert closing.side == Side.buy  # closes a SHORT by buying


def test_already_triggered_bracket_is_not_reevaluated(db, registry, user):
    entry = _entry_order(user.id)
    db.add(entry)
    db.flush()
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.buy, qty=10,
                stop_loss_px=5000.0, take_profit_px=None, status=BracketStatus.triggered,
                entry_order_id=entry.id)
    db.add(b)
    db.commit()

    monitor_brackets(db, registry)  # must not touch an already-resolved bracket

    assert db.query(Order).count() == 1  # still just the entry -- no new closing order created


def test_cancelled_bracket_is_not_reevaluated(db, registry, user):
    entry = _entry_order(user.id)
    db.add(entry)
    db.flush()
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.buy, qty=10,
                stop_loss_px=5000.0, take_profit_px=None, status=BracketStatus.cancelled,
                entry_order_id=entry.id)
    db.add(b)
    db.commit()

    monitor_brackets(db, registry)

    assert db.query(Order).count() == 1


# --- cancel_brackets_closed_elsewhere: the "user or another strategy
# manually closed a bracket-protected position" case ---

def test_a_manual_sell_cancels_an_active_long_bracket_on_the_same_symbol(db, user):
    entry = _entry_order(user.id, side=Side.buy)
    db.add(entry)
    db.flush()
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.buy, qty=10,
                stop_loss_px=1000.0, take_profit_px=None, entry_order_id=entry.id)
    db.add(b)
    db.commit()

    # A manual (or another strategy's) sell fill on the same symbol --
    # the position this bracket was protecting has been touched elsewhere.
    cancel_brackets_closed_elsewhere(db, user_id=user.id, symbol="ICICIBANK", order_side="sell")
    db.commit()

    db.refresh(b)
    assert b.status == BracketStatus.cancelled


def test_a_manual_buy_cancels_an_active_short_bracket_but_not_a_long_one(db, user):
    entry_short = _entry_order(user.id, side=Side.sell)
    entry_long = _entry_order(user.id, side=Side.buy)
    db.add_all([entry_short, entry_long])
    db.flush()
    short_bracket = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.sell,
                             qty=10, stop_loss_px=2000.0, entry_order_id=entry_short.id)
    long_bracket = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.buy,
                            qty=10, stop_loss_px=100.0, entry_order_id=entry_long.id)
    db.add_all([short_bracket, long_bracket])
    db.commit()

    # A buy fill covers a SHORT -- must cancel the short's bracket, but
    # has nothing to do with the long's (a buy only ADDS to a long).
    cancel_brackets_closed_elsewhere(db, user_id=user.id, symbol="ICICIBANK", order_side="buy")
    db.commit()

    db.refresh(short_bracket)
    db.refresh(long_bracket)
    assert short_bracket.status == BracketStatus.cancelled
    assert long_bracket.status == BracketStatus.active


def test_does_not_touch_brackets_on_a_different_symbol(db, user):
    entry = _entry_order(user.id, symbol="HDFCBANK", side=Side.buy)
    db.add(entry)
    db.flush()
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="HDFCBANK", entry_side=Side.buy, qty=10,
                stop_loss_px=1000.0, entry_order_id=entry.id)
    db.add(b)
    db.commit()

    cancel_brackets_closed_elsewhere(db, user_id=user.id, symbol="ICICIBANK", order_side="sell")
    db.commit()

    db.refresh(b)
    assert b.status == BracketStatus.active


def test_a_manual_close_followed_by_bracket_monitoring_does_not_double_close(db, registry, user):
    """End-to-end: submit_order's own call to cancel_brackets_closed_elsewhere
    (exercised via the real router in test_brackets_api.py) is mirrored
    here at the integration level -- a bracket cancelled by a manual close
    must not ALSO fire in monitor_brackets afterward."""
    entry = _entry_order(user.id, side=Side.buy)
    db.add(entry)
    db.flush()
    b = Bracket(user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", entry_side=Side.buy, qty=10,
                stop_loss_px=5000.0, entry_order_id=entry.id)  # would fire immediately if still active
    db.add(b)
    db.commit()

    cancel_brackets_closed_elsewhere(db, user_id=user.id, symbol="ICICIBANK", order_side="sell")
    db.commit()

    monitor_brackets(db, registry)

    db.refresh(b)
    assert b.status == BracketStatus.cancelled  # not "triggered" -- monitor_brackets must have skipped it
    assert b.closing_order_id is None
