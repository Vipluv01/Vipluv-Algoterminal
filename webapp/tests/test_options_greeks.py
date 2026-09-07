"""Portfolio Greeks aggregation and underlying-price stress testing."""

from __future__ import annotations

from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Query, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User
from app.options import chain
from app.options.execution import submit_option_paper_order
from app.options.greeks import get_portfolio_greeks
from app.quant.black_scholes import bsm_greeks


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


def test_flat_book_has_zero_aggregate_greeks_and_no_positions(db, user, registry):
    result = get_portfolio_greeks(db, user, registry)
    assert result.aggregate.delta == 0.0
    assert result.positions == []
    assert result.stress == []


def test_long_call_has_positive_aggregate_delta(db, user, registry):
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=2,
    )
    result = get_portfolio_greeks(db, user, registry)
    assert result.aggregate.delta > 0
    assert len(result.positions) == 1
    assert result.positions[0].qty == 2


def test_short_call_has_negative_aggregate_delta(db, user, registry):
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="sell", qty=1,
    )
    result = get_portfolio_greeks(db, user, registry)
    assert result.aggregate.delta < 0


def test_position_greeks_match_bsm_greeks_scaled_by_qty(db, user, registry):
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    qty = 3
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="PE", strike=strike, expiry_iso=expiry, side="buy", qty=qty,
    )
    result = get_portfolio_greeks(db, user, registry)
    pos = result.positions[0]

    spot = registry.current_prices()["NIFTY50"]
    T = chain.time_to_expiry_years(expiry)
    iv = chain.smile_iv(strike, spot, chain.live_atm_sigma("NIFTY50", registry))
    expected = bsm_greeks(spot, strike, T, chain.RISK_FREE_RATE, iv, "PE")
    assert pos.greeks.delta == pytest.approx(expected.delta * qty)
    assert pos.greeks.gamma == pytest.approx(expected.gamma * qty)
    assert pos.greeks.vega == pytest.approx(expected.vega * qty)


def test_greeks_do_not_fetch_the_users_whole_order_history(db, user, registry):
    """Regression test for a real latency bug measured 2026-09-07: GET
    /options/greeks took ~2.6s on EVERY call (not just a cold one -- it
    had no cache at all). It pulled every paper order the user had ever
    placed -- 108,328 rows, 0.78s just to ORM-materialize -- purely to
    filter down to the option rows and re-walk the whole history through
    compute_account.

    Both halves are now scoped: the option metadata comes from a filtered
    query, and the position snapshot from the shared incremental cache.
    The point of this test is that the numbers are unchanged -- so it
    asserts real greeks on a real position, while pinning that no query
    issued here returns a non-option order."""
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    # A real equity order in the same account, which greeks must not read.
    db.add(Order(
        user_id=user.id, mode=Mode.paper, symbol="ICICIBANK", side=Side.buy,
        order_type=OrderType.market, qty=5, px=None, status=OrderStatus.filled,
        filled_qty=5, avg_fill_px=1250.0,
    ))
    db.commit()
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=2,
    )

    fetched: list[str] = []
    real_all = Query.all

    def _recording_all(self):
        rows = real_all(self)
        fetched.append(str(self))
        return rows

    # The FIRST call is allowed one full walk -- that's the shared account
    # cache being built, and it happens once per process, not per request.
    # What the old code did, and what actually cost 2.6s on every single
    # poll, was paying that walk again on every subsequent call.
    get_portfolio_greeks(db, user, registry)
    with mock.patch.object(Query, "all", _recording_all):
        result = get_portfolio_greeks(db, user, registry)

    assert len(result.positions) == 1
    assert result.positions[0].qty == 2
    assert result.aggregate.delta != 0.0

    assert fetched, "the query recorder should have seen real queries"
    # Only the WHERE clause is examined: a full-entity SELECT names EVERY
    # column (instrument_type included) in its select list, so matching on
    # the whole statement would pass even for the unscoped walk this is
    # meant to catch -- confirmed by reintroducing the bug and watching an
    # earlier version of this assertion stay green.
    unscoped = []
    for statement in fetched:
        if "FROM orders" not in statement:
            continue
        where = " ".join(statement.split("WHERE", 1)[-1].split())
        # Either scoped to option rows only, or an incremental resume of
        # the shared account cache (which reads past a watermark id).
        if "instrument_type" not in where and "orders.id >" not in where:
            unscoped.append(where)
    assert not unscoped, f"greeks should not re-read the full order history: {unscoped}"


def test_closed_position_does_not_appear_in_greeks(db, user, registry):
    expiry = chain.list_expiries()[0].date
    strike = _atm_strike(registry, "NIFTY50")
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=1,
    )
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="sell", qty=1,
    )
    result = get_portfolio_greeks(db, user, registry)
    assert result.positions == []
    assert result.aggregate.delta == 0.0


def test_stress_pnl_is_zero_at_zero_shift_direction_and_positive_for_a_long_call_up_move(db, user, registry):
    # A far-future expiry, NOT one of the two real live expiries from
    # list_expiries() -- those are computed off today's real wall-clock
    # date, and on any day that happens to BE a Thursday (today's own
    # weekly expiry), T floors to ~1 hour, leaving almost no time value:
    # a -2% and a -5% down-move both price the call at ~0 indistinguishably.
    # A genuinely far-dated expiry keeps this test's assertions meaningful
    # regardless of which real calendar day the suite happens to run on.
    expiry = "2028-06-29"
    strike = _atm_strike(registry, "NIFTY50")
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=strike, expiry_iso=expiry, side="buy", qty=1,
    )
    result = get_portfolio_greeks(db, user, registry)
    assert len(result.stress) == 1
    stress = result.stress[0]
    assert stress.underlying == "NIFTY50"

    shifts = {round(row.shift_pct, 4): row for row in stress.rows}
    assert set(shifts) == {-0.05, -0.02, 0.02, 0.05}
    # A long call's P&L must be MONOTONICALLY increasing in the underlying
    # move -- the up-scenarios must show a gain, and the +5% scenario must
    # beat the +2% one.
    assert shifts[0.05].pnl > shifts[0.02].pnl > 0
    assert shifts[-0.05].pnl < shifts[-0.02].pnl < 0


def test_stress_is_broken_out_per_underlying_for_a_multi_underlying_book(db, user, registry):
    expiry = chain.list_expiries()[0].date
    nifty_strike = _atm_strike(registry, "NIFTY50")
    banknifty_strike = _atm_strike(registry, "BANKNIFTY")
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="NIFTY50",
        option_type="CE", strike=nifty_strike, expiry_iso=expiry, side="buy", qty=1,
    )
    submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying="BANKNIFTY",
        option_type="PE", strike=banknifty_strike, expiry_iso=expiry, side="buy", qty=1,
    )
    result = get_portfolio_greeks(db, user, registry)
    underlyings = {s.underlying for s in result.stress}
    assert underlyings == {"NIFTY50", "BANKNIFTY"}


def test_get_greeks_endpoint_returns_aggregate_and_positions(client):
    chain_rows = client.get("/options/chain", params={"underlying": "NIFTY50"}).json()["rows"]
    atm_strike = chain_rows[len(chain_rows) // 2]["strike"]
    expiry = client.get("/options/expiries").json()[0]["date"]
    client.post("/options/orders", json={
        "underlying": "NIFTY50", "option_type": "CE", "strike": atm_strike,
        "expiry": expiry, "side": "buy", "qty": 1,
    })
    resp = client.get("/options/greeks")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["positions"]) == 1
    assert body["aggregate"]["delta"] > 0
    assert len(body["stress"]) == 1
