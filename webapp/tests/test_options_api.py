"""REST surface: option chain/expiries, order submission, and the index-
underlying equity-order rejection (task 2)."""

from __future__ import annotations

from app.markets import DERIVED_INDICES


def test_expiries_endpoint(client):
    resp = client.get("/options/expiries")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["kind"] == "weekly"
    assert all({"date", "label", "kind"} <= set(row) for row in body)


def test_chain_endpoint_returns_21_strikes_for_a_derived_index(client):
    resp = client.get("/options/chain", params={"underlying": "NIFTY50"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["underlying"] == "NIFTY50"
    assert len(body["rows"]) == 21
    assert body["spot"] > 0


def test_chain_endpoint_404s_for_an_unknown_underlying(client):
    resp = client.get("/options/chain", params={"underlying": "NOPE"})
    assert resp.status_code == 404


def test_chain_endpoint_works_for_a_real_equity_underlying_too(client):
    resp = client.get("/options/chain", params={"underlying": "ICICIBANK"})
    assert resp.status_code == 200
    assert resp.json()["underlying"] == "ICICIBANK"


def test_submit_option_order_returns_execution_notice(client):
    chain_rows = client.get("/options/chain", params={"underlying": "NIFTY50"}).json()["rows"]
    strike = chain_rows[len(chain_rows) // 2]["strike"]
    expiry = client.get("/options/expiries").json()[0]["date"]
    resp = client.post("/options/orders", json={
        "underlying": "NIFTY50", "option_type": "CE", "strike": strike,
        "expiry": expiry, "side": "buy", "qty": 1,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Model-priced synthetic option execution" in body["execution_notice"]
    assert body["underlying"] == "NIFTY50"
    assert body["strike"] == strike


def test_submit_option_order_404s_for_unknown_underlying(client):
    resp = client.post("/options/orders", json={
        "underlying": "NOPE", "option_type": "CE", "strike": 100.0,
        "expiry": "2028-01-06", "side": "buy", "qty": 1,
    })
    assert resp.status_code == 404


def test_submit_option_order_blocked_while_halted(client):
    client.get("/risk")
    db = client.db_session_factory()
    try:
        from app.models.risk import RiskSettings
        settings = db.query(RiskSettings).first()
        settings.trading_halted = True
        db.commit()
    finally:
        db.close()

    resp = client.post("/options/orders", json={
        "underlying": "NIFTY50", "option_type": "CE", "strike": 2000.0,
        "expiry": "2028-01-06", "side": "buy", "qty": 1,
    })
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Index underlyings cannot be traded directly as equities (task 2)
# ---------------------------------------------------------------------------

def test_post_orders_rejects_a_derived_index_symbol(client):
    resp = client.post("/orders", json={"symbol": "NIFTY50", "side": "buy", "order_type": "market", "qty": 1})
    assert resp.status_code == 400
    assert "Index underlyings cannot be traded directly as equities" in resp.json()["detail"]


def test_post_orders_algo_rejects_a_derived_index_symbol(client):
    resp = client.post("/orders/algo", json={
        "symbol": "BANKNIFTY", "side": "buy", "total_qty": 10, "algo": "twap", "horizon_bars": 2,
    })
    assert resp.status_code == 400


def test_every_derived_index_is_rejected_for_equity_orders(client):
    for symbol in DERIVED_INDICES:
        resp = client.post("/orders", json={"symbol": symbol, "side": "buy", "order_type": "market", "qty": 1})
        assert resp.status_code == 400, symbol


def test_a_real_equity_symbol_still_trades_normally(client):
    resp = client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /symbols exposes derived indices (task 2)
# ---------------------------------------------------------------------------

def test_symbols_endpoint_marks_derived_indices(client):
    rows = {row["symbol"]: row for row in client.get("/symbols").json()}
    for symbol in DERIVED_INDICES:
        assert rows[symbol]["is_derived"] is True
        assert rows[symbol]["reference_price"] > 0
