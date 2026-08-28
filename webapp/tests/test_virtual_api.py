"""app/routers/virtual.py -- Mode.virtual's own account view. Same
simulated engine as paper mode (see Mode.virtual's own docstring in
models/trading.py), Rs 1 crore starting capital, entirely separate from
paper's own ledger."""

import pytest

from app.accounting import STARTING_VIRTUAL_CASH_DEFAULT


def test_virtual_account_with_no_orders_is_just_starting_cash(client):
    account = client.get("/virtual/account").json()
    assert account["positions"] == []
    assert account["cash"] == STARTING_VIRTUAL_CASH_DEFAULT
    assert account["total_value"] == STARTING_VIRTUAL_CASH_DEFAULT
    assert account["starting_cash"] == STARTING_VIRTUAL_CASH_DEFAULT


def test_virtual_starting_cash_is_not_the_paper_figure(client):
    """The whole point of a separate mode: 1,00,00,000, not 100,000."""
    assert STARTING_VIRTUAL_CASH_DEFAULT == 1_00_00_000.0
    assert STARTING_VIRTUAL_CASH_DEFAULT != 100_000.0


def test_a_virtual_order_does_not_appear_in_the_paper_account(client):
    submit = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5, "mode": "virtual",
    })
    assert submit.status_code == 200, submit.text
    order = submit.json()
    assert order["mode"] == "virtual"
    assert order["filled_qty"] > 0

    paper_account = client.get("/account").json()
    assert paper_account["positions"] == []
    assert paper_account["cash"] == 100_000.0

    virtual_account = client.get("/virtual/account").json()
    pos = next(p for p in virtual_account["positions"] if p["symbol"] == "ICICIBANK")
    assert pos["qty"] == order["filled_qty"]
    assert virtual_account["cash"] < STARTING_VIRTUAL_CASH_DEFAULT


def test_virtual_equity_curve_is_empty_with_no_orders(client):
    assert client.get("/virtual/equity-curve").json() == []


def test_virtual_equity_curve_marks_an_open_position_to_market(client):
    buy = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5, "mode": "virtual",
    }).json()

    curve = client.get("/virtual/equity-curve").json()
    assert len(curve) == 1

    account = client.get("/virtual/account").json()
    assert curve[-1]["equity"] == pytest.approx(account["total_value"])
    # NOT pytest.approx's default relative tolerance for the inequality --
    # against a Rs 1 crore base, its ~1e-6 relative tolerance is a Rs 10
    # absolute window, well bigger than the real (small, tick-spread-sized)
    # move this single fill actually produces.
    assert abs(curve[-1]["equity"] - STARTING_VIRTUAL_CASH_DEFAULT) > 0.01, (
        "an open position must move the curve away from starting cash, same as GET /account/equity-curve"
    )


def test_listing_orders_by_mode_virtual_only_returns_virtual_orders(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    client.post("/orders", json={
        "symbol": "TCS", "side": "buy", "order_type": "market", "qty": 3, "mode": "virtual",
    })

    virtual_orders = client.get("/orders", params={"mode": "virtual"}).json()
    assert len(virtual_orders) == 1
    assert virtual_orders[0]["symbol"] == "TCS"
    assert virtual_orders[0]["mode"] == "virtual"
