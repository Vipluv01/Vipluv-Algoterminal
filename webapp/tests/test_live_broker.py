"""Phase 7 -- app/broker/angelone.py, app/broker/adapter_cache.py, and the
live-order confirm flow in app/routers/orders.py. No real Angel One
account exists to test against, so every test here either exercises the
adapter's own logic against a FAKE SmartConnect (no real network, no real
smartapi-python import -- see _fake_smartapi_module below on why) or
exercises the router with the adapter itself replaced by a stub.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from app.broker.angelone import AngelOneAdapter, AngelOneAuthError, AngelOneCredentials, AngelOneError, AngelOneLiveFeed


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

    def getMarketData(self, mode, exchangeTokens):
        self.calls.append(("getMarketData", (mode, exchangeTokens), {}))
        fetched = []
        for exchange, tokens in exchangeTokens.items():
            for token in tokens:
                if token == "UNFETCHABLE":
                    continue
                if token == "EMPTY_DEPTH":
                    # Angel One's own real convention (confirmed live,
                    # 2026-09-03): an empty depth slot is {price: 0.0,
                    # quantity: 0}, not the absence of a "buy"/"sell" key.
                    buy = [{"price": 0.0, "quantity": 0, "orders": 0}]
                    sell = [{"price": 0.0, "quantity": 0, "orders": 0}]
                else:
                    # Offset well clear of 0 -- token "1" must never
                    # coincidentally produce a real price of exactly 0.0,
                    # which would collide with the empty-slot sentinel above.
                    base = float(token) + 100
                    buy = [{"price": base - 1, "quantity": 10, "orders": 1}]
                    sell = [{"price": base + 1, "quantity": 10, "orders": 1}]
                fetched.append({
                    "exchange": exchange, "tradingSymbol": f"SYM{token}", "symbolToken": token,
                    "ltp": float(token) if token != "EMPTY_DEPTH" else 0.0,
                    "close": (float(token) + 1) if token != "EMPTY_DEPTH" else 0.0,
                    "depth": {"buy": buy, "sell": sell},
                })
        return {"status": True, "message": "SUCCESS", "data": {"fetched": fetched, "unfetched": []}}

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


def test_rate_limited_call_retries_once_on_the_same_session_not_via_relogin(fake_client, monkeypatch):
    """Confirmed live, 2026-09-03: Angel One's real rate-limit rejection
    ("Access denied because of exceeding access rate") used to funnel
    through the SAME path as a genuine session failure -- refresh(), then
    a full re-login. Neither can fix a rate limit; both are themselves
    more real calls that only add to the load causing the limit, and
    login() additionally burns a real TOTP code for nothing. This proves
    a rate-limited call now retries once on the EXISTING session instead
    -- no refresh, no re-login, no lock."""
    monkeypatch.setattr("app.broker.angelone._RATE_LIMIT_RETRY_DELAY_SECONDS", 0)
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    real_client = adapter._client
    calls_before = len(real_client.calls)

    attempts = {"n": 0}

    def rate_limited_then_ok():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ValueError("Couldn't parse the JSON response received from the server: b'Access denied because of exceeding access rate'")
        return "ok"

    result = adapter._call(rate_limited_then_ok)
    assert result == "ok"
    assert attempts["n"] == 2  # exactly one retry, not funneled through refresh/relogin
    assert real_client.calls[calls_before:] == []  # neither generateToken nor generateSession -- same session throughout


def test_rate_limited_call_that_stays_rate_limited_raises_cleanly(fake_client, monkeypatch):
    monkeypatch.setattr("app.broker.angelone._RATE_LIMIT_RETRY_DELAY_SECONDS", 0)
    adapter = AngelOneAdapter(_creds())
    adapter.login()

    def always_rate_limited():
        raise ValueError("Access denied because of exceeding access rate")

    with pytest.raises(AngelOneAuthError, match="still rate-limited"):
        adapter._call(always_rate_limited)


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


def test_concurrent_calls_through_one_shared_adapter_stay_serialized(fake_client):
    """get_adapter_for_user caches ONE AngelOneAdapter per user, and
    Ticker.js's normal polling fires GET /live/market/history for all 7
    named symbols in parallel -- 7 FastAPI sync-route threads calling
    into the SAME adapter instance at once.

    _call_semaphore's bound has a real history worth keeping, not just a
    number: fully serialized (1) closed the original session-mutation
    race but, under actual market-hours latency (a single real call can
    take 0.4-2.5s), made the app visibly hang (confirmed live,
    2026-09-03) -- so this was tried at 4, then narrowed to 2 to get real
    overlap without guessing at an unverified concurrency ceiling.
    Neither held up: at 2, a real 7-symbol burst still drew Angel One's
    "Access denied because of exceeding access rate" on 3 of 7 calls --
    MORE failures than full serialization, no real wall-clock win either.
    That's the same shape as the WS connection-limit incident
    feed_registry.py's own docstring describes -- this REST endpoint
    appears to reject genuinely overlapping requests specifically, not
    just a raw requests-per-second budget serialized calls would also
    hit. Back to 1 until there's real evidence a higher bound is safe;
    resolve_equity_symbol's own cache is the lever that actually helps
    here without re-betting the account on that same assumption twice."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()

    in_flight = 0
    max_concurrent = 0
    state_lock = threading.Lock()

    def _slow_search_scrip(exchange, query):
        nonlocal in_flight, max_concurrent
        with state_lock:
            in_flight += 1
            max_concurrent = max(max_concurrent, in_flight)
        time.sleep(0.02)  # long enough that overlapping calls would reliably be caught
        with state_lock:
            in_flight -= 1
        return {"status": True, "data": [{"exchange": "NSE", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885"}]}

    adapter._client.searchScrip = _slow_search_scrip

    threads = [threading.Thread(target=lambda: adapter.search_symbol_token("NSE", "RELIANCE")) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert max_concurrent == 1, "concurrent calls through the same adapter must never actually overlap"


def test_concurrent_session_failures_never_race_the_relogin_path(fake_client):
    """The actual correctness property _call_lock exists for -- NOT "no
    two calls ever overlap" (the previous, over-strict version of this
    test), but "login()/_refresh() mutating the shared access_token/
    refresh_token/feed_token never runs from two threads at once." Every
    call here is made to fail its first (unlocked) attempt, forcing all
    6 threads into the locked recovery path together -- proving that
    path still fully serializes even though the fast path above no
    longer does."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    adapter._client.searchScrip = lambda exchange, query: (_ for _ in ()).throw(Exception("session expired"))

    login_in_flight = 0
    max_concurrent_logins = 0
    state_lock = threading.Lock()
    real_login = adapter.login

    def _tracked_login():
        nonlocal login_in_flight, max_concurrent_logins
        with state_lock:
            login_in_flight += 1
            max_concurrent_logins = max(max_concurrent_logins, login_in_flight)
        time.sleep(0.02)
        real_login()
        with state_lock:
            login_in_flight -= 1

    adapter.login = _tracked_login
    adapter._refresh = lambda: (_ for _ in ()).throw(Exception("refresh also fails"))

    def _search():
        try:
            adapter.search_symbol_token("NSE", "RELIANCE")
        except Exception:
            pass  # every call is rigged to fail regardless -- only the race matters here

    threads = [threading.Thread(target=_search) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert max_concurrent_logins == 1, "login()/_refresh() must never run concurrently, regardless of read concurrency"


def test_resolve_equity_symbol_never_takes_an_unfiltered_first_match(fake_client):
    """The adapter-level version of the real-money-safety bug regression
    tests above (test_confirming_a_live_order_never_routes_to_the_wrong_
    instrument, test_live_market_api.py's read-path equivalent) -- this
    one exercises AngelOneAdapter.resolve_equity_symbol itself directly,
    against a fake SmartConnect returning the exact real captured SBIN
    result set (14 series, matches[0]=SBIN-AF, plus SBINEQWETF-EQ/
    SBINMID150-EQ substring-decoy tickers that ALSO end in -EQ)."""
    sbin_matches = [
        {"exchange": "NSE", "tradingsymbol": t, "symboltoken": str(200 + i)}
        for i, t in enumerate([
            "SBIN-AF", "SBIN-BE", "SBIN-BL", "SBIN-EQ", "SBIN-IQ", "SBIN-RL", "SBIN-U3", "SBIN-U4",
            "SBINEQWETF-BL", "SBINEQWETF-EQ", "SBINEQWETF-RL",
            "SBINMID150-BL", "SBINMID150-EQ", "SBINMID150-RL",
        ])
    ]
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    adapter._client.search_scrip_result = {"status": True, "data": sbin_matches}

    result = adapter.resolve_equity_symbol("NSE", "SBIN")
    assert result["tradingsymbol"] == "SBIN-EQ"


def test_resolve_equity_symbol_caches_a_successful_result(fake_client):
    """The real lever for reducing Angel One traffic without betting on
    an unverified concurrency ceiling (see the _call_semaphore history
    above) -- ticker->symboltoken is effectively permanent, and every
    real caller (live_market.py's history/WS endpoints, orders.py's
    confirm_live_order) funnels through this one method, so caching it
    here cuts real network calls for anything that looks the same symbol
    up more than once -- which Ticker.js's 30s poll cycle does, forever."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    adapter._client.search_scrip_result = {
        "status": True,
        "data": [{"exchange": "NSE", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885"}],
    }

    first = adapter.resolve_equity_symbol("NSE", "RELIANCE")
    second = adapter.resolve_equity_symbol("NSE", "RELIANCE")
    assert first == second == {"exchange": "NSE", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885"}
    assert sum(1 for c in adapter._client.calls if c[0] == "searchScrip") == 1


def test_resolve_equity_symbol_does_not_cache_a_failed_resolution(fake_client):
    """A transient search failure (or a genuinely unresolvable symbol,
    e.g. TATAMOTORS post-demerger) must not be remembered as permanent --
    only a SUCCESSFUL resolution is cache-worthy."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    adapter._client.search_scrip_result = {"status": True, "data": []}

    with pytest.raises(AngelOneError):
        adapter.resolve_equity_symbol("NSE", "TATAMOTORS")

    adapter._client.search_scrip_result = {
        "status": True,
        "data": [{"exchange": "NSE", "tradingsymbol": "TATAMOTORS-EQ", "symboltoken": "999"}],
    }
    result = adapter.resolve_equity_symbol("NSE", "TATAMOTORS")
    assert result["tradingsymbol"] == "TATAMOTORS-EQ"


def test_resolve_equity_symbol_raises_rather_than_guess_when_no_exact_equity_match_exists(fake_client):
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    adapter._client.search_scrip_result = {
        "status": True,
        "data": [{"exchange": "NSE", "tradingsymbol": "NOTREAL-BE", "symboltoken": "1"}],
    }
    with pytest.raises(AngelOneError):
        adapter.resolve_equity_symbol("NSE", "NOTREAL")


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


def test_get_quote_batch_splits_requests_at_the_confirmed_50_token_limit(fake_client):
    """MAX_TOKENS_PER_BATCH=50 in get_quote_batch is not a guess -- see
    its own docstring: confirmed live against the real account,
    2026-09-03, exactly 50 tokens succeeds and 60 fails with Angel One's
    real "Tokens max limit exceeded" (errorcode AB4029). This proves the
    split actually happens at that boundary, not just that the method
    runs without error under it."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    tokens = [str(i) for i in range(1, 121)]  # 120 tokens -> 3 batches of 50/50/20

    result = adapter.get_quote_batch({"NFO": tokens})

    calls = [c for c in adapter._client.calls if c[0] == "getMarketData"]
    assert len(calls) == 3
    assert [len(c[1][1]["NFO"]) for c in calls] == [50, 50, 20]
    assert len(result) == 120
    assert result["1"].ltp == 1.0
    assert result["1"].best_bid == 100.0
    assert result["1"].best_ask == 102.0


def test_get_quote_batch_merges_across_exchange_segments(fake_client):
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    result = adapter.get_quote_batch({"NSE": ["2885"], "NFO": ["40677"]})
    assert set(result.keys()) == {"2885", "40677"}


def test_get_quote_batch_treats_the_real_empty_depth_sentinel_as_no_quote(fake_client):
    """Angel One's own real convention, confirmed live 2026-09-03: an
    empty side of the book is {price: 0.0, quantity: 0}, not an absent
    key -- a genuine options quote never prices at exactly 0.0 (there's
    always a minimum tick above it), so this is a real, meaningful
    sentinel to treat as "no bid/ask," not a price to report as-is."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    result = adapter.get_quote_batch({"NFO": ["EMPTY_DEPTH"]})
    assert result["EMPTY_DEPTH"].best_bid is None
    assert result["EMPTY_DEPTH"].best_ask is None


def test_get_quote_batch_bids_asks_carry_the_real_depth_levels(fake_client):
    """bids/asks are the FULL depth.buy/depth.sell arrays (DepthLevel
    px/qty pairs), not just the single best_bid/best_ask level those two
    fields already covered -- what a live equity order book actually
    needs to show real resting depth instead of the WS LTP feed's own
    empty bids/asks (see live_market.py's QuoteOut docstring)."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    result = adapter.get_quote_batch({"NSE": ["5"]})
    q = result["5"]
    assert len(q.bids) == 1 and q.bids[0].px == q.best_bid and q.bids[0].qty == 10
    assert len(q.asks) == 1 and q.asks[0].px == q.best_ask and q.asks[0].qty == 10


def test_get_quote_batch_empty_depth_sentinel_yields_empty_bids_asks(fake_client):
    """The same real {price: 0.0, quantity: 0} sentinel best_bid/best_ask
    already treat as "no quote" must not leak into bids/asks as a fake
    zero-price level."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    result = adapter.get_quote_batch({"NFO": ["EMPTY_DEPTH"]})
    assert result["EMPTY_DEPTH"].bids == []
    assert result["EMPTY_DEPTH"].asks == []


def test_get_quote_batch_omits_unfetched_tokens_rather_than_raising(fake_client):
    """A real, expected outcome (an illiquid or delisted token Angel One
    has no current data for) must not fail the whole batch -- the caller
    (a chain display) needs to render "no quote" for ONE contract, not
    lose every other strike over it."""
    adapter = AngelOneAdapter(_creds())
    adapter.login()
    result = adapter.get_quote_batch({"NFO": ["1", "UNFETCHABLE", "2"]})
    assert set(result.keys()) == {"1", "2"}


class _FakeWebSocketApp:
    """Stands in for websocket.WebSocketApp -- captures the callbacks
    AngelOneLiveFeed.start() registers so a test can invoke them directly
    with whatever arity actually broke live, without any real network
    connection (run_forever below returns immediately, no blocking loop)."""

    last_instance: "_FakeWebSocketApp | None" = None

    def __init__(self, url, header=None, on_open=None, on_error=None, on_close=None,
                 on_data=None, on_ping=None, on_pong=None):
        self.on_error = on_error
        self.on_close = on_close
        self.run_forever_called = False
        _FakeWebSocketApp.last_instance = self

    def run_forever(self, **kwargs):
        self.run_forever_called = True


def test_live_feed_on_error_and_on_close_tolerate_the_real_arity_mismatch(monkeypatch):
    """Regression test for a real bug found live against a real Angel One
    account (project-5f): the installed smartapi-python 1.5.5's own
    _on_close is declared to accept only (self, wsapp), but the installed
    websocket-client calls its on_close callback with (wsapp,
    close_status_code, close_msg) -- confirmed via inspect.signature
    against both installed packages, not assumed. Separately, smartapi-
    python's own _on_error internally calls self.on_error with 2 extra
    string args against a base on_error(self) stub that accepts none.
    Both raised TypeError from inside websocket-client's own callback
    dispatch, which combined with the SDK's own auto-reconnect into a
    storm that hit Angel One's real rate limiter repeatedly across
    multiple symbols in a few seconds. AngelOneLiveFeed.start() bypasses
    SmartWebSocketV2.connect() entirely and registers its own safe
    callbacks instead -- this test proves those callbacks tolerate the
    EXACT arities that broke the real SDK's own.
    """
    fake_ws_module = types.ModuleType("websocket")
    fake_ws_module.WebSocketApp = _FakeWebSocketApp
    monkeypatch.setitem(sys.modules, "websocket", fake_ws_module)

    feed = AngelOneLiveFeed(auth_token="jwt-1", api_key="key-1", client_code="C1", feed_token="feed-1")
    feed.start(on_tick=lambda data: None)
    feed._thread.join(timeout=2)

    assert _FakeWebSocketApp.last_instance is not None
    assert _FakeWebSocketApp.last_instance.run_forever_called

    # Must NOT raise -- these are the exact call shapes that broke the
    # real SDK's own on_error/_on_close.
    _FakeWebSocketApp.last_instance.on_error("wsapp-stub", "some error")
    _FakeWebSocketApp.last_instance.on_close("wsapp-stub", 1006, "abnormal closure")


# --- Router-level: POST /orders (mode=live) and POST /orders/{id}/confirm ---


class _StubAdapter:
    def __init__(self, *, order_id="broker-order-999", search_matches=None):
        self.order_id = order_id
        self.search_matches = search_matches if search_matches is not None else [
            {"tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885", "exchange": "NSE"}
        ]
        self.cancelled: list[str] = []
        self.last_place_order_kwargs: dict | None = None

    def search_symbol_token(self, exchange, query):
        return self.search_matches

    def resolve_equity_symbol(self, exchange, symbol):
        # Mirrors AngelOneAdapter.resolve_equity_symbol exactly -- see
        # test_resolve_equity_symbol_never_takes_an_unfiltered_first_match
        # below for why this exact-match filter is load-bearing, not the
        # weaker "ends with -EQ" a first version of the real fix used.
        matches = self.search_symbol_token(exchange, symbol)
        expected = f"{symbol.upper()}-EQ"
        equity_matches = [m for m in matches if str(m.get("tradingsymbol", "")).upper() == expected]
        if not equity_matches:
            raise AngelOneError(f"could not resolve {symbol!r} to a standard NSE equity instrument")
        if len(equity_matches) > 1:
            raise AngelOneError(f"symbol {symbol!r} matched more than one NSE equity entry -- refusing to guess")
        return equity_matches[0]

    def place_order(self, **kwargs):
        self.last_place_order_kwargs = kwargs
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


# Exactly what searchScrip("NSE", "SBIN") returned against a real Angel
# One account (captured live, project-5f) -- 14 series, only SBIN-EQ the
# actual stock. matches[0] would have been SBIN-AF. A naive "ends with
# -EQ" filter is ALSO wrong here: SBINEQWETF-EQ and SBINMID150-EQ are
# DIFFERENT tickers (searchScrip substring-matches "SBIN") whose own -EQ
# entries would make a suffix-only filter ambiguous too -- only an EXACT
# "SBIN-EQ" match is safe. See test_live_market_api.py's identical
# REAL_SBIN_SEARCH_RESULTS for the read-path version of this same test.
REAL_SBIN_SEARCH_RESULTS = [
    {"exchange": "NSE", "tradingsymbol": t, "symboltoken": str(100 + i)}
    for i, t in enumerate([
        "SBIN-AF", "SBIN-BE", "SBIN-BL", "SBIN-EQ", "SBIN-IQ", "SBIN-RL", "SBIN-U3", "SBIN-U4",
        "SBINEQWETF-BL", "SBINEQWETF-EQ", "SBINEQWETF-RL",
        "SBINMID150-BL", "SBINMID150-EQ", "SBINMID150-RL",
    ])
]
SBIN_EQ_TOKEN = next(m["symboltoken"] for m in REAL_SBIN_SEARCH_RESULTS if m["tradingsymbol"] == "SBIN-EQ")


def test_confirming_a_live_order_never_routes_to_the_wrong_instrument(client, monkeypatch):
    """Regression test for a real-money-safety bug found live: confirming
    a live SBIN order must place it against SBIN-EQ's own symboltoken,
    never matches[0] (SBIN-AF -- a different series) and never any of the
    substring-decoy tickers (SBINEQWETF/SBINMID150) that also surface in
    the same search and also end in -EQ."""
    stub = _StubAdapter(search_matches=REAL_SBIN_SEARCH_RESULTS)
    monkeypatch.setattr("app.routers.orders.get_adapter_for_user", lambda db, user_id: stub)
    monkeypatch.setattr("app.routers.orders.notify_order_submitted", lambda **kwargs: None)

    pending = client.post("/orders", json={
        "symbol": "SBIN", "side": "buy", "order_type": "market", "qty": 5, "mode": "live",
    }).json()
    confirmed = client.post(f"/orders/{pending['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert stub.last_place_order_kwargs["symboltoken"] == SBIN_EQ_TOKEN
    assert stub.last_place_order_kwargs["symbol"] == "SBIN-EQ"


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


def test_live_option_order_only_reaches_pending_confirmation_not_the_broker(client):
    """The options analogue of test_live_mode_order_only_reaches_pending_
    confirmation_not_the_broker in test_orders_api.py -- submitting
    mode="live" for an option creates a row and stops, zero broker
    contact, until a separate confirm call."""
    resp = client.post("/options/orders", json={
        "underlying": "NIFTY", "option_type": "CE", "strike": 22250.0,
        "expiry": "06OCT2026", "side": "buy", "qty": 1, "lot_size": 65, "mode": "live",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending_confirmation"
    assert body["avg_fill_px"] is None

    orders = client.get("/orders", params={"mode": "live"}).json()
    order = next(o for o in orders if o["id"] == body["id"])
    assert order["broker_order_id"] is None


def test_confirming_a_live_option_order_resolves_the_real_contract_and_dispatches(client, monkeypatch):
    """The options analogue of test_confirming_a_live_order_dispatches_
    through_the_adapter_and_notifies -- confirm resolves the order's own
    structured fields (underlying/expiry/strike/option_type) through
    resolve_option_contract (not by re-parsing the synthetic symbol
    string), places against the REAL tradingsymbol/token/exchange, and
    converts lots to units (qty * lot_size) for the real broker call."""
    from app.broker.instrument_master import OptionContract

    stub = _StubAdapter()
    monkeypatch.setattr("app.routers.orders.get_adapter_for_user", lambda db, user_id: stub)
    monkeypatch.setattr("app.routers.orders.notify_order_submitted", lambda **kwargs: None)
    fake_contract = OptionContract(
        token="40677", tradingsymbol="NIFTY06OCT2622250CE", underlying="NIFTY",
        expiry="06OCT2026", strike=22250.0, option_type="CE", lot_size=65, exchange_segment="NFO",
    )
    monkeypatch.setattr("app.routers.orders.resolve_option_contract", lambda *a, **k: fake_contract)

    pending = client.post("/options/orders", json={
        "underlying": "NIFTY", "option_type": "CE", "strike": 22250.0,
        "expiry": "06OCT2026", "side": "buy", "qty": 2, "lot_size": 65, "mode": "live",
    }).json()
    confirmed = client.post(f"/orders/{pending['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "submitted"

    kwargs = stub.last_place_order_kwargs
    assert kwargs["symbol"] == "NIFTY06OCT2622250CE"
    assert kwargs["symboltoken"] == "40677"
    assert kwargs["exchange"] == "NFO"
    assert kwargs["qty"] == 130  # 2 lots * 65 lot_size, NOT 2 raw units
    assert kwargs["product_type"] == "CARRYFORWARD"  # NOT the equity path's default INTRADAY


def test_confirming_a_live_option_order_with_no_real_contract_is_rejected_not_stuck(client, monkeypatch):
    """resolve_option_contract returning None (the real, expected outcome
    for a strike/expiry the exchange doesn't actually list) must become
    a clear terminal rejection, never a silent guess and never an order
    stuck in pending_confirmation forever."""
    stub = _StubAdapter()
    monkeypatch.setattr("app.routers.orders.get_adapter_for_user", lambda db, user_id: stub)
    monkeypatch.setattr("app.routers.orders.resolve_option_contract", lambda *a, **k: None)

    pending = client.post("/options/orders", json={
        "underlying": "NIFTY", "option_type": "CE", "strike": 99999.0,
        "expiry": "06OCT2026", "side": "buy", "qty": 1, "lot_size": 65, "mode": "live",
    }).json()
    resp = client.post(f"/orders/{pending['id']}/confirm")
    assert resp.status_code == 502
    assert stub.last_place_order_kwargs is None  # never reached place_order at all

    orders = client.get("/orders", params={"mode": "live"}).json()
    rejected = next(o for o in orders if o["id"] == pending["id"])
    assert rejected["status"] == "rejected"
