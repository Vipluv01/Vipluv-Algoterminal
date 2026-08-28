"""Angel One SmartAPI adapter -- the one place this codebase talks to a
real broker over the network. Everything else (routers/orders.py's live
dispatch, routers/live_market.py) goes through this class, never the
`SmartApi` SDK directly, so the broker's own quirks (see below) stay
contained to one file.

Written against the `smartapi-python` SDK's ACTUAL source (read directly
out of the installed wheel, not just docs/blog paraphrase -- SmartAPI's
own docs site is a JS SPA that doesn't render for automated fetching).
Field/method names below (generateSession, placeOrder's param dict,
rmsLimit's response keys, SmartWebSocketV2's constructor/subscribe/tick
fields) are read from that source and about as reliable as anything short
of a live account can be. What's NOT independently verified from a
primary source, flagged inline where it matters: the exact position()
response shape, the full historical `interval` enum, and whether
last_traded_price over the WebSocket feed is paise (i.e. divide by 100)
or already rupees -- verify against one real tick before trusting the
scaling in _ws_tick_to_payload below.

Two real side effects of importing `SmartApi` worth knowing, NEITHER of
which this module can avoid short of vendoring the SDK: (1)
`SmartApi.smartConnect`'s module body makes a live HTTP call to
api.ipify.org to guess this machine's public IP, at IMPORT time -- ~1-2s,
falls back to a hardcoded IP on failure; (2) both SmartConnect and
SmartWebSocketV2 unconditionally create a `logs/<date>/app.log` file
relative to the process's cwd on construction. Because of (1) especially,
`SmartApi` is imported LAZILY inside the functions that need it below,
never at this module's top level -- importing app.broker.angelone itself
(e.g. for a type hint) must stay fast and network-free, in particular so
the test suite and ordinary app startup never pay this cost or require
network access.

Credential discipline (hard rule, not a suggestion): api_key/client_code/
password/totp_secret arrive here already decrypted by the caller and are
held only as instance attributes of one AngelOneAdapter for this
process's lifetime -- never logged, never included in an exception
message, never returned from any method. Broker-side error text (e.g.
"Invalid TOTP") is safe to surface since it originates from Angel One's
own response, not from echoing anything we submitted.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

import pyotp


class AngelOneError(Exception):
    pass


class AngelOneAuthError(AngelOneError):
    pass


# SmartWebSocketV2's exchangeType codes (see its own docstring on
# subscribe()) -- NSE cash-market equities is what every symbol this app
# trades (NAMED_INSTRUMENTS-style tickers) lives on.
EXCHANGE_TYPE_NSE_CM = 1

# getCandleData's documented interval values -- only the ones directly
# confirmed against SDK/forum examples (ONE_MINUTE, FIFTEEN_MINUTE,
# ONE_HOUR, ONE_DAY); a fuller enum is commonly cited but not confirmed
# from a primary source, so this app only exposes what's confirmed rather
# than guessing at unconfirmed values.
CANDLE_INTERVALS: dict[str, str] = {
    "1m": "ONE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "1hr": "ONE_HOUR",
    "1d": "ONE_DAY",
}


@dataclass
class AngelOneCredentials:
    """Already-decrypted -- the caller (app/broker/adapter_cache.py)
    decrypts LiveBrokerCredential's Fernet-encrypted columns and builds
    this immediately before constructing an adapter; nothing in this
    dataclass is ever written back to the database or a log line."""

    api_key: str
    client_code: str
    password: str  # encrypted_api_secret's slot, repurposed -- see LiveBrokerCredential's own docstring
    totp_secret: str


class AngelOneAdapter:
    """One broker session for one user. Not thread-safe for concurrent
    calls on the SAME instance (matches this codebase's existing "one
    engine per goroutine" discipline elsewhere) -- app/broker/adapter_cache.py
    hands out one instance per user, and FastAPI's per-request handling
    already serializes a single user's own requests in practice (one
    browser tab, one order at a time)."""

    def __init__(self, creds: AngelOneCredentials):
        self._creds = creds
        self._client = None  # SmartConnect instance, built on first login()
        self._jwt_token: str | None = None
        self._refresh_token: str | None = None
        self._feed_token: str | None = None
        self._logged_in_at: float | None = None

    # -- session -------------------------------------------------------

    def _totp_now(self) -> str:
        return pyotp.TOTP(self._creds.totp_secret).now()

    def login(self) -> None:
        """Full login -- always gets a fresh jwt/refresh/feed token triple.
        Called on first use, and as the last-resort fallback when a
        refresh-token renewal also fails (see _call below)."""
        from SmartApi import SmartConnect  # lazy -- see this module's own docstring

        if self._client is None:
            self._client = SmartConnect(api_key=self._creds.api_key)
        result = self._client.generateSession(
            self._creds.client_code, self._creds.password, self._totp_now(),
        )
        if not result or not result.get("status"):
            message = (result or {}).get("message", "login failed")
            raise AngelOneAuthError(f"Angel One login failed: {message}")
        data = result["data"]
        self._jwt_token = data["jwtToken"]
        self._refresh_token = data["refreshToken"]
        self._feed_token = data["feedToken"]
        self._logged_in_at = time.monotonic()

    def _refresh(self) -> None:
        """Cheaper than a full login (no TOTP needed) -- SmartAPI sessions
        are documented (community-sourced, not an official TTL table) to
        expire on their own schedule, so this is tried FIRST on any
        session-shaped failure before falling back to login()."""
        if self._client is None or self._refresh_token is None:
            raise AngelOneAuthError("no active session to refresh")
        response = self._client.generateToken(self._refresh_token)
        if not response or "data" not in response:
            raise AngelOneAuthError("Angel One session refresh failed")
        self._jwt_token = response["data"]["jwtToken"]
        self._feed_token = response["data"]["feedToken"]
        self._logged_in_at = time.monotonic()

    def _call(self, fn: Callable[[], dict]):
        """Runs one SmartConnect call, transparently handling session
        expiry: refresh-token renewal first (cheap, no TOTP), then a full
        re-login (needs a fresh TOTP) if that also fails, then propagates.
        "Don't assume one login lasts the process lifetime" -- this is
        that assumption never being made anywhere calls actually happen.

        The very FIRST login (self._client is None) is inside this same
        try, not a bare call before it -- a real bug this shipped with
        and was caught against a live dev server (an invalid TOTP secret
        format raises from pyotp before any network call even happens,
        and that exception was escaping this method entirely, surfacing
        callers as a bare unhandled 500 instead of the clean
        AngelOneAuthError every caller here is written to expect and
        catch). Every failure this method can produce now funnels through
        the same refresh -> re-login -> raise AngelOneAuthError path,
        regardless of whether it happened on the first call ever made or
        the hundredth.
        """
        try:
            if self._client is None:
                self.login()
            return fn()
        except Exception as first_exc:
            try:
                self._refresh()
                return fn()
            except Exception:
                pass
            try:
                self.login()
                return fn()
            except Exception as final_exc:
                raise AngelOneAuthError(
                    f"Angel One call failed after refresh+re-login attempts: {final_exc}"
                ) from first_exc

    def ensure_session(self) -> None:
        """Logs in if this adapter has never logged in yet -- a no-op
        otherwise. For a caller (routers/live_market.py's WS endpoint)
        that needs a live jwt/feed token BEFORE its first `_call`-wrapped
        request (AngelOneLiveFeed's constructor needs them directly, not
        through `_call`), rather than reaching into `_call`/`login`
        itself."""
        if self._client is None:
            self.login()

    # -- trading ---------------------------------------------------------

    def search_symbol_token(self, exchange: str, query: str) -> list[dict]:
        """exchange e.g. "NSE"; query e.g. "RELIANCE" -- returns whatever
        SmartAPI's own searchScrip finds (each item has at least exchange/
        tradingsymbol/symboltoken), so a caller can resolve a human symbol
        to the symboltoken every other call below needs, without this app
        maintaining its own copy of Angel One's instrument master."""
        response = self._call(lambda: self._client.searchScrip(exchange, query))
        if not response or not response.get("status"):
            return []
        return response.get("data") or []

    def place_order(
        self, *, symbol: str, symboltoken: str, exchange: str, side: str, qty: int,
        order_type: str = "MARKET", product_type: str = "INTRADAY", price: float | None = None,
        variety: str = "NORMAL",
    ) -> str:
        """Returns the broker's own order id (a string) -- the ONLY proof
        (per Order.broker_order_id's own docstring in models/trading.py)
        that a live order actually reached Angel One. side is "buy"/"sell"
        (this app's own convention, matching Side); transactiontype below
        is SmartAPI's own BUY/SELL spelling."""
        params = {
            "variety": variety,
            "tradingsymbol": symbol,
            "symboltoken": symboltoken,
            "transactiontype": "BUY" if side == "buy" else "SELL",
            "exchange": exchange,
            "ordertype": order_type,
            "producttype": product_type,
            "duration": "DAY",
            "price": str(price) if price is not None else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
        }
        order_id = self._call(lambda: self._client.placeOrder(params))
        if not order_id:
            raise AngelOneError("Angel One rejected the order (no order id returned)")
        return order_id

    def cancel_order(self, broker_order_id: str, variety: str = "NORMAL") -> None:
        response = self._call(lambda: self._client.cancelOrder(broker_order_id, variety))
        if not response or not response.get("status"):
            message = (response or {}).get("message", "cancel failed")
            raise AngelOneError(f"Angel One order cancel failed: {message}")

    def get_positions(self) -> list[dict]:
        response = self._call(lambda: self._client.position())
        if not response or not response.get("status"):
            return []
        return response.get("data") or []

    def get_funds(self) -> dict:
        """Field names confirmed from the official Go SDK's json tags:
        availablecash, net, m2munrealized, m2mrealized, etc -- all
        lowercase, no camelCase. Returned as-is (not remapped to this
        app's own naming) so a caller can see exactly what Angel One sent."""
        response = self._call(lambda: self._client.rmsLimit())
        if not response or not response.get("status"):
            return {}
        return response.get("data") or {}

    def get_historical_candles(
        self, *, exchange: str, symboltoken: str, interval: str, from_dt: datetime, to_dt: datetime,
    ) -> list[dict]:
        """interval is one of CANDLE_INTERVALS' own short keys (e.g.
        "1m"), translated to SmartAPI's own enum string. Returns a list of
        {timestamp_ms, open, high, low, close, volume} dicts -- the same
        shape routers/live_market.py converts into BarOut, mirroring
        routers/market.py's existing HistoryOut/BarOut as closely as
        possible so the frontend's chart consumer doesn't need to know
        which mode it's looking at."""
        if interval not in CANDLE_INTERVALS:
            raise AngelOneError(f"unsupported interval {interval!r} -- one of {sorted(CANDLE_INTERVALS)}")
        params = {
            "exchange": exchange,
            "symboltoken": symboltoken,
            "interval": CANDLE_INTERVALS[interval],
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        response = self._call(lambda: self._client.getCandleData(params))
        if not response or not response.get("status"):
            message = (response or {}).get("message", "historical candle request failed")
            raise AngelOneError(f"Angel One historical candles failed: {message}")
        bars = []
        for row in response.get("data") or []:
            # [timestamp (ISO, +05:30 offset), open, high, low, close, volume] --
            # confirmed array order from SDK source/test fixtures.
            ts_iso, o, h, l, c, v = row
            ts_ms = int(datetime.fromisoformat(ts_iso).astimezone(timezone.utc).timestamp() * 1000)
            bars.append({"timestamp_ms": ts_ms, "open": o, "high": h, "low": l, "close": c, "volume": v})
        return bars


class AngelOneLiveFeed:
    """Thin wrapper around SmartWebSocketV2 -- LTP-mode only (this app has
    no use yet for QUOTE/SNAP_QUOTE/DEPTH's extra fields). SmartWebSocketV2
    is callback-based and its own connect() call is BLOCKING (it runs
    websocket-client's run_forever() loop) -- start() below runs that on a
    dedicated daemon thread and hands parsed ticks back to `on_tick`,
    which is called FROM THAT THREAD, not the caller's. A caller bridging
    this into an asyncio WebSocket route (routers/live_market.py) is
    responsible for getting each tick back onto the event loop thread
    (e.g. via loop.call_soon_threadsafe), the same cross-thread handoff
    discipline app/broker/notify.py's fire-and-forget notifier uses for
    the opposite direction.
    """

    def __init__(self, *, auth_token: str, api_key: str, client_code: str, feed_token: str):
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # lazy -- see module docstring

        self._ws = SmartWebSocketV2(auth_token, api_key, client_code, feed_token)
        self._thread: threading.Thread | None = None

    def start(self, *, on_tick: Callable[[dict], None], on_open: Callable[[], None] | None = None) -> None:
        self._ws.on_data = lambda _wsapp, data: on_tick(data)
        if on_open is not None:
            self._ws.on_open = lambda _wsapp: on_open()
        self._thread = threading.Thread(target=self._ws.connect, daemon=True)
        self._thread.start()

    def subscribe(self, tokens: list[str], correlation_id: str = "algoterminal") -> None:
        self._ws.subscribe(correlation_id, self._ws.LTP_MODE, [{"exchangeType": EXCHANGE_TYPE_NSE_CM, "tokens": tokens}])

    def stop(self) -> None:
        self._ws.close_connection()
