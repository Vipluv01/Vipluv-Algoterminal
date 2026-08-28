"""End-to-end API tests: real FastAPI app, real bourse Engine per symbol
(via the lifespan-created MarketRegistry), a fresh in-memory DB per test.
This is the same "drive it for real, not just unit-test the pieces"
discipline used to catch real bugs earlier this session (the WebSocket
upgrade / static-file collision, the NaN-order-type wording)."""

import pytest

from app.main import app


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_market_order_buy_fills_against_seed_liquidity(client):
    resp = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "ICICIBANK"
    assert body["status"] in ("filled", "partially_filled")
    assert body["filled_qty"] > 0
    assert body["avg_fill_px"] is not None


def test_live_mode_is_rejected_with_501(client):
    resp = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5, "mode": "live",
    })
    assert resp.status_code == 501


def test_limit_order_without_price_is_rejected(client):
    resp = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "limit", "qty": 5,
    })
    assert resp.status_code == 400


def test_unknown_symbol_is_a_404(client):
    resp = client.post("/orders", json={
        "symbol": "NOTASYMBOL", "side": "buy", "order_type": "market", "qty": 5,
    })
    assert resp.status_code == 404


def test_stop_limit_order_requires_both_px_and_stop_px(client):
    # stop_px alone: the engine has nowhere to rest the order once triggered.
    resp = client.post("/orders", json={
        "symbol": "TCS", "side": "buy", "order_type": "stop_limit", "qty": 5, "stop_px": 4300.0,
    })
    assert resp.status_code == 400

    # px alone: no trigger, so it isn't really a stop order.
    resp = client.post("/orders", json={
        "symbol": "TCS", "side": "buy", "order_type": "stop_limit", "qty": 5, "px": 4300.0,
    })
    assert resp.status_code == 400


def test_stop_limit_order_rests_untriggered_and_can_be_cancelled(client):
    # TCS starts ~4150. A buy stop triggers on price rising THROUGH stop_px,
    # so a stop_px well above the current market (4500) and a limit price
    # above that (4550) should sit untouched in the stop book rather than
    # firing immediately.
    submit = client.post("/orders", json={
        "symbol": "TCS", "side": "buy", "order_type": "stop_limit",
        "qty": 5, "px": 4550.0, "stop_px": 4500.0,
    })
    assert submit.status_code == 200, submit.text
    order = submit.json()
    assert order["order_type"] == "stop_limit"
    assert order["px"] == 4550.0
    assert order["stop_px"] == 4500.0
    assert order["status"] == "submitted"
    assert order["filled_qty"] == 0

    cancel = client.delete(f"/orders/{order['id']}")
    assert cancel.status_code == 200, cancel.text

    listing = client.get("/orders").json()
    cancelled = next(o for o in listing if o["id"] == order["id"])
    assert cancelled["status"] == "cancelled"


def test_resting_limit_order_appears_in_open_orders_then_can_be_cancelled(client):
    # TCS starts at ~4150 (NAMED_INSTRUMENTS in app/markets.py); a price
    # needs to stay inside [s0*0.5, s0*2.0] to be valid at all, and low
    # enough here to rest as a passive buy without crossing the seeded ask.
    submit = client.post("/orders", json={
        "symbol": "TCS", "side": "buy", "order_type": "limit", "qty": 5, "px": 3000.0,
    })
    assert submit.status_code == 200, submit.text
    order = submit.json()
    assert order["status"] == "submitted"
    assert order["filled_qty"] == 0

    cancel = client.delete(f"/orders/{order['id']}")
    assert cancel.status_code == 200, cancel.text

    listing = client.get("/orders").json()
    cancelled = next(o for o in listing if o["id"] == order["id"])
    assert cancelled["status"] == "cancelled"


def test_cancelling_an_already_filled_order_is_rejected(client):
    submit = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    })
    order = submit.json()
    assert order["status"] == "filled"

    cancel = client.delete(f"/orders/{order['id']}")
    assert cancel.status_code == 400


def test_list_orders_returns_only_this_users_orders_newest_first(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1})
    client.post("/orders", json={"symbol": "HDFCBANK", "side": "buy", "order_type": "market", "qty": 1})
    listing = client.get("/orders").json()
    assert len(listing) == 2
    assert listing[0]["symbol"] == "HDFCBANK"  # most recent first


def test_list_orders_x_total_count_header_reflects_the_real_count(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1})
    client.post("/orders", json={"symbol": "HDFCBANK", "side": "buy", "order_type": "market", "qty": 1})
    resp = client.get("/orders")
    assert resp.headers["x-total-count"] == "2"
    assert len(resp.json()) == 2


def test_list_orders_filters_by_symbol(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1})
    client.post("/orders", json={"symbol": "HDFCBANK", "side": "buy", "order_type": "market", "qty": 1})
    resp = client.get("/orders", params={"symbol": "HDFCBANK"})
    listing = resp.json()
    assert len(listing) == 1
    assert listing[0]["symbol"] == "HDFCBANK"
    assert resp.headers["x-total-count"] == "1"


def test_list_orders_filters_by_status(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1})  # fills
    client.post("/orders", json={
        "symbol": "TCS", "side": "buy", "order_type": "limit", "qty": 1, "px": 3000.0,
    })  # rests, unfilled

    filled = client.get("/orders", params={"status": "filled"}).json()
    assert all(o["status"] == "filled" for o in filled)
    assert len(filled) == 1

    submitted = client.get("/orders", params={"status": "submitted"}).json()
    assert all(o["status"] == "submitted" for o in submitted)
    assert len(submitted) == 1


def test_list_orders_filters_by_strategy(client):
    client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1, "strategy_key": "alpha_rsi_ema",
    })
    client.post("/orders", json={"symbol": "HDFCBANK", "side": "buy", "order_type": "market", "qty": 1})

    strategy_only = client.get("/orders", params={"strategy": "alpha_rsi_ema"}).json()
    assert len(strategy_only) == 1
    assert strategy_only[0]["symbol"] == "ICICIBANK"


def test_list_orders_filters_by_date_range_excludes_orders_outside_it(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1})

    far_future = client.get("/orders", params={"date_from": "2099-01-01T00:00:00"}).json()
    assert far_future == []

    far_past = client.get("/orders", params={"date_to": "1999-01-01T00:00:00"}).json()
    assert far_past == []

    wide_open = client.get("/orders", params={"date_from": "2000-01-01T00:00:00", "date_to": "2099-01-01T00:00:00"}).json()
    assert len(wide_open) == 1


def test_list_orders_limit_and_offset_paginate_without_changing_total_count(client):
    for symbol in ("ICICIBANK", "HDFCBANK", "RELIANCE"):
        client.post("/orders", json={"symbol": symbol, "side": "buy", "order_type": "market", "qty": 1})

    page1 = client.get("/orders", params={"limit": 2, "offset": 0})
    page2 = client.get("/orders", params={"limit": 2, "offset": 2})
    assert page1.headers["x-total-count"] == "3"
    assert page2.headers["x-total-count"] == "3"
    assert len(page1.json()) == 2
    assert len(page2.json()) == 1
    # no overlap between pages
    ids_page1 = {o["id"] for o in page1.json()}
    ids_page2 = {o["id"] for o in page2.json()}
    assert ids_page1.isdisjoint(ids_page2)


def test_list_orders_limit_cannot_exceed_the_page_size_ceiling(client):
    resp = client.get("/orders", params={"limit": 100_000})
    assert resp.status_code == 422


def test_account_reflects_a_filled_buy(client):
    submit = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    })
    order = submit.json()
    assert order["filled_qty"] > 0

    account = client.get("/account").json()
    pos = next(p for p in account["positions"] if p["symbol"] == "ICICIBANK")
    assert pos["qty"] == order["filled_qty"]
    assert account["cash"] < 100_000.0  # starting cash, spent buying


def test_account_with_no_orders_is_just_starting_cash(client):
    account = client.get("/account").json()
    assert account["positions"] == []
    assert account["cash"] == 100_000.0
    assert account["total_value"] == 100_000.0


def test_equity_curve_is_empty_with_no_orders(client):
    assert client.get("/account/equity-curve").json() == []


def test_equity_curve_marks_an_open_position_to_market_not_just_realized_pnl(client):
    """The bug this endpoint was fixed for: while a position is open, the
    curve must move with mark-to-market value, not sit flat at starting
    cash the way the old realized-only curve did."""
    buy = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    }).json()

    curve = client.get("/account/equity-curve").json()
    assert len(curve) == 1
    mark = app.state.registry.markets["ICICIBANK"].price_history[-1]
    expected = 100_000.0 - buy["filled_qty"] * buy["avg_fill_px"] + buy["filled_qty"] * mark
    assert curve[0]["equity"] == pytest.approx(expected)

    account = client.get("/account").json()
    assert curve[-1]["equity"] == pytest.approx(account["total_value"]), (
        "mark-to-market equity must agree with GET /account's own total_value "
        "at the point where no fill is pending -- this is the exact disagreement "
        "the bug report described"
    )


def test_equity_curve_ends_at_realized_equity_once_the_position_is_fully_closed(client):
    buy = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    }).json()
    sell = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "sell", "order_type": "market", "qty": buy["filled_qty"],
    }).json()

    curve = client.get("/account/equity-curve").json()
    assert len(curve) == 2

    account = client.get("/account").json()
    # Fully flat by the end -- no open position left for mark-to-market to
    # disagree about, so this must equal starting_cash + realized P&L,
    # exactly like the old realized-only curve did for this same case.
    assert curve[-1]["equity"] == pytest.approx(100_000.0 + account["total_realized_pnl"])
    assert curve[-1]["equity"] == pytest.approx(account["total_value"])
    assert sell["filled_qty"] >= 0  # the closing order may rest partially -- not the point of this test


def test_equity_curve_moves_when_price_history_advances_between_fills(client):
    """Advancing the market via SymbolMarket.step() (the same helper
    test_leaderboard_api.py uses) must move the curve's mark -- proving
    it's genuinely reading price_history, not frozen at each fill's own
    fill price forever."""
    client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    })
    before = client.get("/account/equity-curve").json()[-1]["equity"]

    market = app.state.registry.markets["ICICIBANK"]
    for _ in range(5):
        market.step()

    after = client.get("/account/equity-curve").json()[-1]["equity"]
    account = client.get("/account").json()
    assert after == pytest.approx(account["total_value"])
    if market.price_history[-1] != market.price_history[0]:
        assert after != pytest.approx(before)
