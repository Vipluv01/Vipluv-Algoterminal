"""End-to-end API tests: real FastAPI app, real bourse Engine per symbol
(via the lifespan-created MarketRegistry), a fresh in-memory DB per test.
This is the same "drive it for real, not just unit-test the pieces"
discipline used to catch real bugs earlier this session (the WebSocket
upgrade / static-file collision, the NaN-order-type wording)."""


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
