from app.markets import NAMED_INSTRUMENTS


def test_symbols_endpoint_lists_the_named_instruments(client):
    resp = client.get("/symbols")
    assert resp.status_code == 200
    symbols = {row["symbol"] for row in resp.json()}
    assert symbols == set(NAMED_INSTRUMENTS.keys())


def test_market_ws_sends_an_immediate_snapshot_on_connect(client):
    with client.websocket_connect("/ws/market/ICICIBANK") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "tick"
        assert msg["symbol"] == "ICICIBANK"
        assert msg["price"] > 0
        # Seed liquidity means best_bid/best_ask exist from the very first
        # connection, before any tick has run (DISABLE_MARKET_TICK=1 in
        # tests -- see conftest.py).
        assert msg["best_bid"] is not None
        assert msg["best_ask"] is not None
        assert len(msg["bids"]) >= 1
        assert len(msg["asks"]) >= 1


def test_market_ws_prices_are_in_currency_not_raw_ticks(client):
    """Regression check for a real unit bug caught before it shipped:
    best_bid/best_ask/depth come back from the engine in integer ticks,
    not currency -- ICICIBANK's seed price is ~1250, so a value in the
    thousands of ticks (e.g. 24990) instead would be exactly this bug."""
    with client.websocket_connect("/ws/market/ICICIBANK") as ws:
        msg = ws.receive_json()
        assert 500 < msg["best_bid"] < 3000
        assert 500 < msg["best_ask"] < 3000
        assert 500 < msg["bids"][0]["px"] < 3000


def test_market_ws_rejects_an_unknown_symbol(client):
    try:
        with client.websocket_connect("/ws/market/NOTASYMBOL"):
            pass
        assert False, "expected the connection to be rejected"
    except Exception:
        pass  # starlette's test client raises on a closed-during-handshake websocket
