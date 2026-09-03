"""app/routers/live_market.py -- GET /live/market/history, mirroring
market.py's HistoryOut/BarOut shape against a stubbed AngelOneAdapter (no
real Angel One account exists to test against; see the router's own
docstring)."""

from __future__ import annotations

from datetime import timedelta

from app.broker.angelone import AngelOneAuthError, AngelOneError


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

    def resolve_equity_symbol(self, exchange, symbol):
        # Mirrors AngelOneAdapter.resolve_equity_symbol's own filtering
        # exactly, over this stub's own symbol_matches -- a test double
        # for the real method, not a shortcut around it.
        matches = self.search_symbol_token(exchange, symbol)
        expected = f"{symbol.upper()}-EQ"
        equity_matches = [
            m for m in matches
            if m.get("exchange") == "NSE" and str(m.get("tradingsymbol", "")).upper() == expected
        ]
        if not equity_matches:
            raise AngelOneError(
                f"could not resolve {symbol!r} to a standard NSE equity instrument "
                f"(found {len(matches)} matching series, none an exact {expected!r} match)"
            )
        if len(equity_matches) > 1:
            raise AngelOneError(f"symbol {symbol!r} matched more than one NSE equity entry -- refusing to guess")
        return equity_matches[0]

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


def test_live_history_unknown_symbol_is_a_clean_502_not_a_wrong_instrument(client, monkeypatch):
    stub = _StubAdapter(symbol_matches=[])
    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: stub)

    resp = client.get("/live/market/history", params={"symbol": "NOTAREALSYMBOL"})
    assert resp.status_code == 502
    assert "NOTAREALSYMBOL" in resp.json()["detail"]


# Exactly what searchScrip("NSE", "SBIN") returned against a real Angel
# One account (captured live) -- 14 series, only one (SBIN-EQ) the actual
# stock. Also exercises the subtler part of the bug: SBINEQWETF-*/
# SBINMID150-* are DIFFERENT tickers that happen to contain "SBIN" as a
# substring (searchScrip does substring search) AND their own "-EQ" entry
# also ends in "-EQ" -- so a naive "ends with -EQ" filter is not enough
# either; it would find three -EQ-suffixed entries here, not one.
REAL_SBIN_SEARCH_RESULTS = [
    {"exchange": "NSE", "tradingsymbol": t, "symboltoken": str(100 + i)}
    for i, t in enumerate([
        "SBIN-AF", "SBIN-BE", "SBIN-BL", "SBIN-EQ", "SBIN-IQ", "SBIN-RL", "SBIN-U3", "SBIN-U4",
        "SBINEQWETF-BL", "SBINEQWETF-EQ", "SBINEQWETF-RL",
        "SBINMID150-BL", "SBINMID150-EQ", "SBINMID150-RL",
    ])
]
SBIN_EQ_TOKEN = next(m["symboltoken"] for m in REAL_SBIN_SEARCH_RESULTS if m["tradingsymbol"] == "SBIN-EQ")


def test_live_history_never_takes_the_first_match_when_it_is_not_the_equity_series(client, monkeypatch):
    """Regression test for a real bug found live against a real Angel One
    account, using the exact captured result set (see
    REAL_SBIN_SEARCH_RESULTS above). matches[0] (SBIN-AF) is not the
    actual stock, and a naive "ends with -EQ" filter is ALSO wrong here
    (SBINEQWETF-EQ and SBINMID150-EQ both end in -EQ too) -- only an
    EXACT "SBIN-EQ" match is safe. This is a real-money-safety bug for the
    order-confirm path (see test_live_broker.py's own version) and a
    correctness bug here: showing chart data for the wrong instrument."""
    stub = _StubAdapter(symbol_matches=REAL_SBIN_SEARCH_RESULTS)
    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: stub)

    resp = client.get("/live/market/history", params={"symbol": "SBIN"})
    assert resp.status_code == 200, resp.text
    assert stub.last_candle_params["symboltoken"] == SBIN_EQ_TOKEN


def test_live_history_widens_the_lookback_window_so_it_survives_a_closed_market(client, monkeypatch):
    """Regression test for a real bug found live against the real account,
    2026-09-03 21:12 IST: `from_dt = now() - (limit * interval)` alone is
    anchored to WALL-CLOCK now(), not the market's actual last trading
    session. At the default 1m/300-bar chart load, that's a 5-hour
    lookback -- once NSE has been closed a few hours past 15:30, the
    ENTIRE window falls in dead time and Angel One's real candle API
    (which only returns bars for minutes that actually traded, confirmed
    by that same live call returning zero) hands back nothing, so the
    chart renders empty. The fix floors the lookback at 10 calendar days
    regardless of the natural `limit * interval` span or what time it is
    right now -- this asserts that floor directly rather than depending
    on the test happening to run outside market hours itself."""
    stub = _StubAdapter()
    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: stub)

    resp = client.get("/live/market/history", params={"symbol": "RELIANCE", "interval": "1m", "limit": 300})
    assert resp.status_code == 200, resp.text
    span = stub.last_candle_params["to_dt"] - stub.last_candle_params["from_dt"]
    assert span >= timedelta(days=10)


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
