from app.markets import DERIVED_INDICES, NAMED_INSTRUMENTS


def test_symbols_endpoint_lists_the_named_instruments(client):
    resp = client.get("/symbols")
    assert resp.status_code == 200
    rows = {row["symbol"]: row for row in resp.json()}
    # Every real, simulated equity, PLUS the derived index baskets
    # (app/markets.py's DERIVED_INDICES, phase 5) -- this endpoint now
    # covers both, distinguished by is_derived.
    assert set(rows) == set(NAMED_INSTRUMENTS.keys()) | set(DERIVED_INDICES.keys())
    for symbol in NAMED_INSTRUMENTS:
        assert rows[symbol]["is_derived"] is False
    for symbol in DERIVED_INDICES:
        assert rows[symbol]["is_derived"] is True


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


def test_market_ws_streams_a_derived_index(client):
    """Regression test for a real bug: the WebSocket gate only checked
    NAMED_INSTRUMENTS, so NIFTY50/BANKNIFTY (DERIVED_INDICES) were closed
    immediately on connect (code 4404) and their charts never received a
    single tick. A derived index has no real order book, so best_bid/
    best_ask/bids/asks must come back empty/None -- honest about there
    being no matched instrument behind it -- while price is still real."""
    with client.websocket_connect("/ws/market/NIFTY50") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "tick"
        assert msg["symbol"] == "NIFTY50"
        assert msg["price"] > 0
        assert msg["best_bid"] is None
        assert msg["best_ask"] is None
        assert msg["bids"] == []
        assert msg["asks"] == []


def test_market_ws_streams_bank_nifty_too(client):
    with client.websocket_connect("/ws/market/BANKNIFTY") as ws:
        msg = ws.receive_json()
        assert msg["symbol"] == "BANKNIFTY"
        assert msg["price"] > 0


def test_market_ws_rejects_an_unknown_symbol(client):
    try:
        with client.websocket_connect("/ws/market/NOTASYMBOL"):
            pass
        assert False, "expected the connection to be rejected"
    except Exception:
        pass  # starlette's test client raises on a closed-during-handshake websocket
