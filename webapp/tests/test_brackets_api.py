def test_submitting_an_order_with_sl_tp_creates_an_active_bracket(client):
    resp = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
        "stop_loss_px": 1000.0, "take_profit_px": 1500.0,
    })
    assert resp.status_code == 200, resp.text
    order = resp.json()
    assert order["filled_qty"] > 0  # DISABLE_MARKET_TICK=1 keeps the seed book deterministic

    brackets = client.get("/orders/brackets").json()
    assert len(brackets) == 1
    b = brackets[0]
    assert b["symbol"] == "ICICIBANK"
    assert b["entry_side"] == "buy"
    assert b["stop_loss_px"] == 1000.0
    assert b["take_profit_px"] == 1500.0
    assert b["status"] == "active"
    assert b["qty"] == order["filled_qty"]


def test_order_without_sl_tp_creates_no_bracket(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    assert client.get("/orders/brackets").json() == []


def test_an_order_that_never_fills_creates_no_bracket(client):
    # A far out-of-range limit price never crosses -- rests instead of filling.
    client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "limit", "qty": 5, "px": 700.0,
        "stop_loss_px": 500.0, "take_profit_px": 900.0,
    })
    assert client.get("/orders/brackets").json() == []


def test_cancelling_an_active_bracket(client):
    client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
        "stop_loss_px": 1000.0, "take_profit_px": None,
    })
    bracket_id = client.get("/orders/brackets").json()[0]["id"]

    resp = client.delete(f"/orders/brackets/{bracket_id}")
    assert resp.status_code == 200

    assert client.get("/orders/brackets").json() == []  # active-only listing no longer shows it


def test_cancelling_an_already_cancelled_bracket_is_rejected(client):
    client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5, "stop_loss_px": 1000.0,
    })
    bracket_id = client.get("/orders/brackets").json()[0]["id"]
    client.delete(f"/orders/brackets/{bracket_id}")

    resp = client.delete(f"/orders/brackets/{bracket_id}")
    assert resp.status_code == 400


def test_cancelling_a_nonexistent_bracket_is_a_404(client):
    resp = client.delete("/orders/brackets/99999")
    assert resp.status_code == 404
