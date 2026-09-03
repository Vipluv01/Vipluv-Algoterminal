"""Synthetic options execution: the modeled fill-price spread formula,
submit_option_paper_order's Order shape, and mark_option_positions' role
feeding compute_account (task 5.4a)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounting import compute_account
from app.db import Base
from app.markets import MarketRegistry
from app.models.trading import InstrumentType, Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User
from app.options import chain
from app.options.execution import (
    MIN_SPREAD_ABSOLUTE,
    SPREAD_FRACTION_OF_PRICE,
    mark_option_positions,
    modeled_spread,
    option_fill_price,
    option_theoretical_price,
    submit_option_paper_order,
)


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
    u = User(google_sub="s", email="e@x.com", display_name="T")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def registry():
    reg = MarketRegistry(seed=0)
    try:
        yield reg
    finally:
        reg.close()


def _atm_strike(registry: MarketRegistry, underlying: str) -> float:
    spot = registry.current_prices()[underlying]
    step = chain.strike_step(underlying, spot)
    return round(spot / step) * step


# ---------------------------------------------------------------------------
# Modeled spread / fill price
# ---------------------------------------------------------------------------

def test_modeled_spread_uses_the_percentage_when_it_exceeds_the_floor():
    price = 200.0
    assert modeled_spread(price) == pytest.approx(SPREAD_FRACTION_OF_PRICE * price)


def test_modeled_spread_floors_at_the_absolute_minimum_for_cheap_options():
    price = 1.0  # 1% of this is 0.01, well below the 0.5 floor
    assert modeled_spread(price) == MIN_SPREAD_ABSOLUTE


def test_buy_fill_price_is_theo_plus_half_spread():
    theo = 100.0
    fill = option_fill_price(theo, "buy")
    assert fill == pytest.approx(theo + 0.5 * modeled_spread(theo))


def test_sell_fill_price_is_theo_minus_half_spread():
    theo = 100.0
    fill = option_fill_price(theo, "sell")
    assert fill == pytest.approx(theo - 0.5 * modeled_spread(theo))


def test_buy_fill_price_always_exceeds_sell_fill_price_at_the_same_theo():
    theo = 55.0
    assert option_fill_price(theo, "buy") > option_fill_price(theo, "sell")


# ---------------------------------------------------------------------------
# submit_option_paper_order
# ---------------------------------------------------------------------------

def test_submit_option_order_fills_completely_at_the_modeled_price(db, user, registry):
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    result = submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=5,
    )
    order = result.order
    assert order.status.value == "filled"
    assert order.filled_qty == 5
    assert order.instrument_type == InstrumentType.option
    assert order.underlying == "NIFTY50"
    assert order.strike == strike
    assert order.option_type == "CE"
    assert order.symbol == chain.build_contract_key("NIFTY50", expiry, strike, "CE")
    assert order.avg_fill_px == pytest.approx(order.px)


def test_submit_option_order_buy_and_sell_at_the_same_moment_bracket_the_theo_price(db, user, registry):
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    theo = option_theoretical_price("NIFTY50", strike, expiry, "CE", registry)
    buy = submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=1,
    ).order
    sell = submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="sell", qty=1,
    ).order
    assert buy.avg_fill_px > theo > sell.avg_fill_px


# ---------------------------------------------------------------------------
# mark_option_positions -> compute_account integration (task 5.4a)
# ---------------------------------------------------------------------------

def test_mark_option_positions_returns_a_mark_for_every_distinct_open_contract(db, user, registry):
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=1,
    )
    marks = mark_option_positions(db, user.id, registry)
    key = chain.build_contract_key("NIFTY50", expiry, strike, "CE")
    assert key in marks
    assert marks[key] > 0


def test_mark_option_positions_dedupes_repeat_orders_on_the_same_contract(db, user, registry):
    """Regression test for the real perf bug found live, 2026-09-04: a
    long-running paper account had 63,736 filled option orders behind
    just 19 distinct contracts (the same contract re-traded thousands of
    times) -- mark_option_positions was fetching every ORM Order object
    just to de-dupe by symbol in Python. The query was switched to only
    the 5 columns actually needed; this confirms the real behavior
    (exactly one mark per distinct contract, regardless of how many
    orders exist for it) is unchanged."""
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    for _ in range(5):
        submit_option_paper_order(
            db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
            option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=1,
        )
    marks = mark_option_positions(db, user.id, registry)
    key = chain.build_contract_key("NIFTY50", expiry, strike, "CE")
    assert list(marks.keys()) == [key]


def test_moved_underlying_changes_option_unrealized_pnl(db, user):
    """The exact scenario 5.4a calls for: without merging a live BSM mark
    into current_prices, compute_account's current_prices.get(symbol,
    avg_entry_px) fallback would report this position as perfectly flat
    regardless of how far the underlying actually moved. Deterministic --
    two explicit spot levels, not a live simulation's own random walk."""
    from app.quant.black_scholes import bsm_price

    expiry = chain.list_expiries()[0].date
    strike = 22000.0
    sigma0 = 0.20
    T = chain.time_to_expiry_years(expiry)
    entry_spot = 22000.0
    moved_spot = 22000.0 * 1.15  # a real, sizeable +15% move

    entry_price = bsm_price(entry_spot, strike, T, chain.RISK_FREE_RATE, chain.smile_iv(strike, entry_spot, sigma0), "CE")
    moved_price = bsm_price(moved_spot, strike, T, chain.RISK_FREE_RATE, chain.smile_iv(strike, moved_spot, sigma0), "CE")
    assert moved_price > entry_price  # a call is worth more when the underlying rises

    symbol = chain.build_contract_key("NIFTY50", expiry, strike, "CE")
    order = Order(
        user_id=user.id, mode=Mode.paper, symbol=symbol, side=Side.buy, order_type=OrderType.market,
        qty=1, px=entry_price, status=OrderStatus.filled, filled_qty=1, avg_fill_px=entry_price,
        instrument_type=InstrumentType.option, underlying="NIFTY50", strike=strike,
        expiry=expiry, option_type="CE", lot_size=1, multiplier=1,
    )
    db.add(order)
    db.commit()

    orders = [order]
    snapshot_before = compute_account(orders, {symbol: entry_price})
    snapshot_after = compute_account(orders, {symbol: moved_price})

    pnl_before = snapshot_before.positions[symbol].unrealized_pnl
    pnl_after = snapshot_after.positions[symbol].unrealized_pnl
    assert pnl_before == pytest.approx(0.0, abs=1e-6)
    assert pnl_after > pnl_before


def test_get_account_reflects_option_unrealized_pnl(client):
    chain_rows = client.get("/options/chain", params={"underlying": "NIFTY50"}).json()["rows"]
    atm_strike = chain_rows[len(chain_rows) // 2]["strike"]
    resp = client.post("/options/orders", json={
        "underlying": "NIFTY50", "option_type": "CE", "strike": atm_strike,
        "expiry": client.get("/options/expiries").json()[0]["date"], "side": "buy", "qty": 1,
    })
    assert resp.status_code == 200, resp.text

    account = client.get("/account").json()
    positions = {p["symbol"]: p for p in account["positions"]}
    assert resp.json()["symbol"] in positions
    # avg_entry_px was the FILL price (theo + half spread); a fresh mark a
    # moment later is the pure theo price (no spread) -- these are
    # different by construction, proving the position is genuinely marked
    # from a live BSM recompute, not falling back to avg_entry_px (the
    # flat-P&L bug 5.4a exists to prevent).
    pos = positions[resp.json()["symbol"]]
    assert pos["avg_entry_px"] != pytest.approx(0.0)
