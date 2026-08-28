"""app/routers/live_market.py -- GET /live/market/history, mirroring
market.py's HistoryOut/BarOut shape against a stubbed AngelOneAdapter (no
real Angel One account exists to test against; see the router's own
docstring)."""

from __future__ import annotations

from app.broker.angelone import AngelOneAuthError


class _StubAdapter:
    def __init__(self, *, bars=None, symbol_matches=None):
        self.bars = bars if bars is not None else [
            {"timestamp_ms": 1_700_000_000_000, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1000},
            {"timestamp_ms": 1_700_000_060_000, "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "volume": 1200},
        ]
        self.symbol_matches = symbol_matches if symbol_matches is not None else [
            {"exchange": "NSE", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885"}
        ]
        self.last_candle_params = None

    def search_symbol_token(self, exchange, query):
        return self.symbol_matches

    def get_historical_candles(self, **kwargs):
        self.last_candle_params = kwargs
        return self.bars


def test_live_history_mirrors_market_historys_bar_shape(client, monkeypatch):
    stub = _StubAdapter()
    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: stub)

    resp = client.get("/live/market/history", params={"symbol": "RELIANCE", "interval": "1m", "limit": 50})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert body["interval"] == "1m"
    assert body["returned_bars"] == 2
    assert body["bars"][0]["open"] == 100.0
    assert body["bars"][0]["timestamp"] == 1_700_000_000_000
    assert stub.last_candle_params["symboltoken"] == "2885"
    assert stub.last_candle_params["exchange"] == "NSE"


def test_live_history_with_no_broker_credential_is_a_400(client):
    resp = client.get("/live/market/history", params={"symbol": "RELIANCE"})
    assert resp.status_code == 400


def test_live_history_unknown_symbol_is_a_404(client, monkeypatch):
    stub = _StubAdapter(symbol_matches=[])
    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: stub)

    resp = client.get("/live/market/history", params={"symbol": "NOTAREALSYMBOL"})
    assert resp.status_code == 404


def test_live_history_respects_the_limit_ceiling(client):
    resp = client.get("/live/market/history", params={"symbol": "RELIANCE", "limit": 5000})
    assert resp.status_code == 422


def test_live_history_returns_a_clean_502_not_a_bare_500_when_symbol_lookup_fails(client, monkeypatch):
    """Regression test for a real bug (found live, bourse1 testing with a
    deliberately invalid TOTP secret): search_symbol_token raising was
    escaping every try/except in this router entirely, since only
    HTTPException gets FastAPI's automatic clean-response handling --
    any other exception type is just as much an unhandled 500 as one
    nobody thought to catch at all."""
    class _FailingAdapter(_StubAdapter):
        def search_symbol_token(self, exchange, query):
            raise AngelOneAuthError("Angel One login failed: Invalid TOTP")

    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: _FailingAdapter())

    resp = client.get("/live/market/history", params={"symbol": "RELIANCE"})
    assert resp.status_code == 502
    assert "Invalid TOTP" in resp.json()["detail"]
