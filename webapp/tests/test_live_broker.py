"""Phase 7 -- app/broker/angelone.py, app/broker/adapter_cache.py, and the
live-order confirm flow in app/routers/orders.py. No real Angel One
account exists to test against, so every test here either exercises the
adapter's own logic against a FAKE SmartConnect (no real network, no real
smartapi-python import -- see _fake_smartapi_module below on why) or
exercises the router with the adapter itself replaced by a stub.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.broker.angelone import AngelOneAdapter, AngelOneAuthError, AngelOneCredentials, AngelOneError


def _creds() -> AngelOneCredentials:
    # JBSWY3DPEHPK3PXP is pyotp's own well-known example base32 seed (from
    # its README) -- not a real Angel One secret, just a valid TOTP seed
    # so AngelOneAdapter._totp_now() has something real to compute against.
    return AngelOneCredentials(
        api_key="test-api-key", client_code="C123456", password="test-password",
        totp_secret="JBSWY3DPEHPK3PXP",
    )


class _FakeSmartConnect:
    """Stands in for SmartApi.SmartConnect -- captures every call this
    test cares about and returns realistic-shaped responses (field names
    taken directly from the installed smartapi-python 1.5.5 wheel's own
    source, see angelone.py's own docstring)."""

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.calls: list[tuple[str, tuple, dict]] = []
        self.generate_session_result = {
            "status": True,
            "data": {"jwtToken": "jwt-1", "refreshToken": "refresh-1", "feedToken": "feed-1"},
        }
        self.generate_token_result = {"data": {"jwtToken": "jwt-2", "feedToken": "feed-2"}}
        self.place_order_result = "broker-order-123"
        self.search_scrip_result = {
            "status": True,
            "data": [{"exchange": "NSE", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885"}],
        }

    def generateSession(self, client_code, password, totp):
        self.calls.append(("generateSession", (client_code, password, totp), {}))
        return self.generate_session_result

    def generateToken(self, refresh_token):
        self.calls.append(("generateToken", (refresh_token,), {}))
        return self.generate_token_result

    def placeOrder(self, params):
        self.calls.append(("placeOrder", (params,), {}))
        return self.place_order_result

    def cancelOrder(self, order_id, variety):
        self.calls.append(("cancelOrder", (order_id, variety), {}))
        return {"status": True}

    def searchScrip(self, exchange, searchscrip):
        self.calls.append(("searchScrip", (exchange, searchscrip), {}))
        return self.search_scrip_result

    def position(self):
        return {"status": True, "data": [{"tradingsymbol": "RELIANCE-EQ", "netqty": "10"}]}

    def rmsLimit(self):
        return {"status": True, "data": {"availablecash": "50000.00", "net": "50000.00"}}

    def getCandleData(self, params):
        self.calls.append(("getCandleData", (params,), {}))
        return {
            "status": True,
            "data": [
                ["2026-01-01T09:15:00+05:30", 100.0, 101.0, 99.5, 100.5, 1000],
                ["2026-01-01T09:16:00+05:30", 100.5, 102.0, 100.0, 101.5, 1200],
            ],
        }


@pytest.fixture
def fake_client():
    """Patches the adapter's lazy `from SmartApi import SmartConnect` to
    resolve to our fake class WITHOUT ever importing the real `SmartApi`
    package -- importing the real one makes a live network call to
    api.ipify.org at class-definition time (confirmed directly against
    the installed wheel; see angelone.py's own docstring), which has no
    place in a unit test. Registering a fake module under sys.modules
    short-circuits Python's import machinery before it ever reaches the
    real package."""
    fake_module = types.ModuleType("SmartApi")
    fake_module.SmartConnect = _FakeSmartConnect
    sys.modules["SmartApi"] = fake_module
    yield _FakeSmartConnect
    del sys.modules["SmartApi"]


def test_totp_now_is_a_six_digit_code(fake_client):
    adapter = AngelOneAdapter(_creds())
    code = adapter._totp_now()
    assert len(code) == 6
    assert code.isdigit()


def test_login_stores_tokens_and_sends_a_real_totp(fake_client):
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    assert adapter._jwt_token == "jwt-1"
    assert adapter._refresh_token == "refresh-1"
    assert adapter._feed_token == "feed-1"

    call = adapter._client.calls[0]
    assert call[0] == "generateSession"
    client_code, password, totp = call[1]
    assert client_code == "C123456"
    assert password == "test-password"
    assert len(totp) == 6 and totp.isdigit()


def test_login_failure_raises_and_never_leaks_the_password_or_totp_secret(fake_client):
    def _build(api_key=None):
        c = _FakeSmartConnect(api_key)
        c.generate_session_result = {"status": False, "message": "Invalid TOTP"}
        return c

    sys.modules["SmartApi"].SmartConnect = _build

    adapter = AngelOneAdapter(_creds())
    with pytest.raises(AngelOneAuthError) as exc_info:
        adapter.login()
    message = str(exc_info.value)
    assert "Invalid TOTP" in message
    assert "test-password" not in message
    assert "JBSWY3DPEHPK3PXP" not in message


def test_call_falls_back_to_refresh_then_full_relogin_on_failure(fake_client):
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    real_client = adapter._client

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("session expired")
        return "ok"

    result = adapter._call(flaky)
    assert result == "ok"
    assert attempts["n"] == 3  # 1st call fails, refresh lets 2nd fail too, re-login lets 3rd succeed
    assert any(c[0] == "generateToken" for c in real_client.calls)
    # Once for the explicit adapter.login() above, once more for _call's
    # own fallback re-login after the refresh-then-retry also failed.
    assert sum(1 for c in real_client.calls if c[0] == "generateSession") == 2


def test_the_very_first_call_ever_made_still_raises_angelone_error_not_a_raw_exception(fake_client):
    """Regression test for a real bug, found live: an invalid TOTP secret
    (e.g. "T" -- not valid base32) raises from pyotp INSIDE login(),
    before any network call happens. When that login was the FIRST one
    (adapter._client is None), _call used to run it OUTSIDE its own
    try/except, so the raw pyotp/binascii exception escaped this method
    entirely -- every router calling through here (live_market.py,
    orders.py's confirm endpoint) is written to catch AngelOneError
    specifically, so an uncaught raw exception surfaced as a bare
    unhandled 500 with no detail instead of the clean 400/502 those
    routers are designed to return."""
    bad_creds = AngelOneCredentials(
        api_key="test-api-key", client_code="C123456", password="test-password", totp_secret="T",
    )
    adapter = AngelOneAdapter(bad_creds)
    with pytest.raises(AngelOneError):
        adapter.search_symbol_token("NSE", "RELIANCE")


def test_place_order_returns_the_broker_order_id(fake_client):
    adapter = AngelOneAdapter(_creds())
    order_id = adapter.place_order(
        symbol="RELIANCE-EQ", symboltoken="2885", exchange="NSE", side="buy", qty=5,
    )
    assert order_id == "broker-order-123"
    params = adapter._client.calls[-1][1][0]
    assert params["transactiontype"] == "BUY"
    assert params["tradingsymbol"] == "RELIANCE-EQ"
    assert params["quantity"] == "5"


def test_place_order_raises_when_broker_returns_no_order_id(fake_client):
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    adapter._client.place_order_result = None
    with pytest.raises(AngelOneError):
        adapter.place_order(symbol="RELIANCE-EQ", symboltoken="2885", exchange="NSE", side="buy", qty=5)


def test_search_symbol_token_returns_the_broker_matches(fake_client):
    adapter = AngelOneAdapter(_creds())
    matches = adapter.search_symbol_token("NSE", "RELIANCE")
    assert matches[0]["symboltoken"] == "2885"


def test_get_historical_candles_parses_the_confirmed_array_order(fake_client):
    from datetime import datetime

    adapter = AngelOneAdapter(_creds())
    bars = adapter.get_historical_candles(
        exchange="NSE", symboltoken="2885", interval="1m",
        from_dt=datetime(2026, 1, 1, 9, 15), to_dt=datetime(2026, 1, 1, 9, 20),
    )
    assert len(bars) == 2
    assert bars[0]["open"] == 100.0
    assert bars[0]["close"] == 100.5
    assert bars[1]["volume"] == 1200
    assert isinstance(bars[0]["timestamp_ms"], int)


def test_get_historical_candles_rejects_an_unsupported_interval(fake_client):
    from datetime import datetime

    adapter = AngelOneAdapter(_creds())
    with pytest.raises(AngelOneError):
        adapter.get_historical_candles(
            exchange="NSE", symboltoken="2885", interval="3m",
            from_dt=datetime(2026, 1, 1), to_dt=datetime(2026, 1, 2),
        )


# --- Router-level: POST /orders (mode=live) and POST /orders/{id}/confirm ---


class _StubAdapter:
    def __init__(self, *, order_id="broker-order-999", search_matches=None):
        self.order_id = order_id
        self.search_matches = search_matches if search_matches is not None else [
            {"tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885", "exchange": "NSE"}
        ]
        self.cancelled: list[str] = []

    def search_symbol_token(self, exchange, query):
        return self.search_matches

    def place_order(self, **kwargs):
        return self.order_id

    def cancel_order(self, broker_order_id, variety="NORMAL"):
        self.cancelled.append(broker_order_id)


def test_confirming_a_live_order_dispatches_through_the_adapter_and_notifies(client, monkeypatch):
    stub = _StubAdapter()
    monkeypatch.setattr("app.routers.orders.get_adapter_for_user", lambda db, user_id: stub)
    notified = {}
    monkeypatch.setattr(
        "app.routers.orders.notify_order_submitted",
        lambda **kwargs: notified.update(kwargs),
    )

    pending = client.post("/orders", json={
        "symbol": "RELIANCE", "side": "buy", "order_type": "market", "qty": 5, "mode": "live",
    }).json()
    assert pending["status"] == "pending_confirmation"

    confirmed = client.post(f"/orders/{pending['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "submitted"
    assert body["broker_order_id"] == "broker-order-999"
    assert body["confirmed_at"] is not None
    assert notified == {"symbol": "RELIANCE", "side": "buy", "qty": 5, "broker_order_id": "broker-order-999"}


def test_confirming_a_live_order_the_broker_rejects_marks_it_rejected_not_stuck_pending(client, monkeypatch):
    class _RejectingAdapter(_StubAdapter):
        def search_symbol_token(self, exchange, query):
            return []  # nothing found -- angelone.py raises AngelOneError for this

    monkeypatch.setattr("app.routers.orders.get_adapter_for_user", lambda db, user_id: _RejectingAdapter())

    pending = client.post("/orders", json={
        "symbol": "NOTAREALSYMBOL", "side": "buy", "order_type": "market", "qty": 5, "mode": "live",
    }).json()
    resp = client.post(f"/orders/{pending['id']}/confirm")
    assert resp.status_code == 502

    orders = client.get("/orders", params={"mode": "live"}).json()
    rejected = next(o for o in orders if o["id"] == pending["id"])
    assert rejected["status"] == "rejected"


def test_confirming_twice_is_rejected_the_second_time(client, monkeypatch):
    stub = _StubAdapter()
    monkeypatch.setattr("app.routers.orders.get_adapter_for_user", lambda db, user_id: stub)
    monkeypatch.setattr("app.routers.orders.notify_order_submitted", lambda **kwargs: None)

    pending = client.post("/orders", json={
        "symbol": "RELIANCE", "side": "buy", "order_type": "market", "qty": 5, "mode": "live",
    }).json()
    first = client.post(f"/orders/{pending['id']}/confirm")
    assert first.status_code == 200
    second = client.post(f"/orders/{pending['id']}/confirm")
    assert second.status_code == 400
