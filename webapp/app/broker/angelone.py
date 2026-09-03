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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

import pyotp


class AngelOneError(Exception):
    pass


class AngelOneAuthError(AngelOneError):
    pass


# Angel One's real rate-limit rejection ("Access denied because of
# exceeding access rate") isn't valid JSON, so it never reaches app code
# as a clean typed exception -- it surfaces as a parse failure whose
# string form still carries this exact substring (confirmed live,
# 2026-09-03). String-matching an error message is fragile in general,
# but this one is distinctive enough, and the alternative (treating every
# rate-limit rejection as a session failure worth a real re-login) is the
# actively worse failure mode _call's own docstring explains.
_RATE_LIMIT_SIGNATURE = "exceeding access rate"


def _is_rate_limited(exc: Exception) -> bool:
    return _RATE_LIMIT_SIGNATURE in str(exc)


# One short wait before a single retry on the SAME session -- long enough
# for Angel One's own rate window to plausibly roll over, short enough
# not to make an already-slow call feel broken. Not backed by a
# documented rate-limit window (Angel One doesn't publish one for this
# endpoint); revisit with real evidence if this turns out to be the
# wrong order of magnitude.
_RATE_LIMIT_RETRY_DELAY_SECONDS = 1.5

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


@dataclass(frozen=True)
class DepthLevel:
    px: float
    qty: int


@dataclass(frozen=True)
class Quote:
    """One real quote from getMarketData's FULL mode -- see
    AngelOneAdapter.get_quote_batch's own docstring on why ltp alone
    isn't enough (a real, confirmed staleness problem for illiquid
    contracts) and best_bid/best_ask come from the live order book
    instead. Any field can be None -- a genuinely unquoted side of the
    book, or a contract Angel One has no data for at all, are both real,
    distinct, expected states, not error conditions.

    bids/asks carry FULL mode's own multi-level depth.buy/depth.sell
    (best_bid/best_ask are just their own [0] element, kept as separate
    fields since most callers -- the options chain -- only ever needed
    the single best level). Real Angel One responses pad depth.buy/
    depth.sell to a fixed number of slots even when fewer levels are
    genuinely resting, using the SAME {price: 0.0, quantity: 0} sentinel
    already established for an empty best-level slot -- those trailing
    empty slots are dropped here, not included as fake zero-price rows."""

    ltp: float | None
    close: float | None
    best_bid: float | None
    best_ask: float | None
    # Defaulted (not required) so existing callers/tests constructing a
    # Quote for just ltp/close/best_bid/best_ask (most of them, since
    # full multi-level depth is a newer, live-equity-order-book-specific
    # need) don't all need updating -- get_quote_batch, the one real
    # production constructor, always passes these explicitly regardless.
    bids: list[DepthLevel] = field(default_factory=list)
    asks: list[DepthLevel] = field(default_factory=list)


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
    hands out one instance per user, and _call below coordinates every
    real call through it -- an EARLIER version of this docstring claimed
    "FastAPI's per-request handling already serializes a single user's
    own requests in practice," which is wrong and was disproven live:
    Ticker.js's normal polling fires all 7 named symbols' GET
    /live/market/history requests in parallel (7 simultaneous sync-route
    threads, all sharing this one cached adapter), and that produced
    nondeterministic per-request failures under concurrency that never
    reproduced when the same 7 calls were made sequentially.

    A FIRST fix (fully serializing every call, including successful ones,
    through one lock for its entire duration) closed that race but traded
    it for a real, separately-found problem: during actual NSE market
    hours a single real Angel One call can itself take 0.4-2.5s, and with
    everything -- Ticker's 8-symbol poll, a chart's WS setup, an order
    confirm -- funneling through ONE lock for the full round trip each,
    the app visibly hung/degraded under real load (confirmed live,
    2026-09-03, during market hours). _call below keeps only what
    actually needs mutual exclusion (login/_refresh mutating the shared
    access_token/refresh_token/feed_token) behind a lock; a successful
    fn() call on an already-valid session runs WITHOUT holding it, so
    concurrent reads can genuinely overlap instead of queueing behind
    each other one at a time.
    """

    def __init__(self, creds: AngelOneCredentials):
        self._creds = creds
        self._client = None  # SmartConnect instance, built on first login()
        self._jwt_token: str | None = None
        self._refresh_token: str | None = None
        self._feed_token: str | None = None
        self._logged_in_at: float | None = None
        # Guards ONLY login()/_refresh()'s mutation of the shared
        # access_token/refresh_token/feed_token -- see _call's own
        # docstring. Not reentrant on purpose: nothing inside
        # login/_refresh ever calls back into _call on the same thread,
        # so a plain Lock (not RLock) is correct and catches an
        # accidental future reentrant call as a deadlock during testing,
        # rather than silently allowing it.
        self._call_lock = threading.Lock()
        # Bounds how many real HTTP calls to Angel One this adapter lets
        # run at once. Tried 4, then 2, in response to real evidence --
        # confirmed live, 2026-09-03: even at 2 concurrent, a 7-symbol
        # burst still drew "Access denied because of exceeding access
        # rate" on 3 of 7 calls, MORE failures than at full serialization,
        # with no real wall-clock win either. That's the same shape as
        # the WS connection-limit incident feed_registry.py's own
        # docstring describes -- Angel One's REST endpoint appears to
        # reject genuinely OVERLAPPING requests specifically, not just a
        # raw requests-per-second budget sequential calls would also hit.
        # Set to 1 (fully serialized reads, same as the very first fix)
        # until there's real evidence a higher bound is actually safe --
        # see resolve_equity_symbol's own cache for the real lever that
        # DOES help here without betting the account on an unverified
        # concurrency assumption a second time.
        self._call_semaphore = threading.Semaphore(1)
        # resolve_equity_symbol's result, keyed by (exchange, symbol) --
        # ticker->symboltoken is effectively permanent for this adapter's
        # lifetime (barring a rare corporate action, which would surface
        # as a clear broker-side rejection on the stale token, not a
        # silent wrong-instrument route -- the safety property that cache
        # exists to protect is unaffected by caching a static identifier).
        # Every real caller (live_market.py's history/WS endpoints AND
        # orders.py's confirm_live_order) funnels through resolve_equity_
        # symbol, so this halves the real Angel One traffic for anything
        # that looks the same symbol up more than once -- which Ticker.js's
        # own 30s poll cycle does, every cycle, for every symbol, forever.
        self._resolved_symbol_cache: dict[tuple[str, str], dict] = {}

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

        Two-tier concurrency control, not one lock around everything --
        see the class docstring for why a single full-duration lock (the
        first fix for the original race) turned into its own real
        problem under actual market-hours load. self._call_semaphore
        bounds how many real HTTP calls run at once (throughput/broker-
        load concern, not correctness). self._call_lock guards ONLY the
        session-mutating recovery path (login/_refresh writing
        self._client's shared tokens) -- the ACTUAL race this whole
        design exists to prevent: two threads independently deciding
        their call failed and racing to refresh/relogin at the same
        time, each invalidating the session the other was about to use.
        A successful fn() call on an already-valid session (the common
        case) never touches self._call_lock at all, so concurrent reads
        genuinely overlap instead of queueing one at a time behind
        whichever call happened to go first.

        A rate-limit response is NOT a session failure, and is handled
        separately from the refresh/re-login path below -- confirmed
        live, 2026-09-03: Angel One's real error for this is "Access
        denied because of exceeding access rate" (not valid JSON, so it
        surfaces as a parse error, not a clean typed exception). Routing
        that through refresh()/login() like a real session failure would
        was actively counterproductive: neither can fix a rate limit,
        each is itself another real call that only adds to the load
        causing the limit, and login() additionally burns a real TOTP
        code for no reason. A rate-limited call instead gets ONE short
        wait-and-retry on the SAME session -- no lock, no mutation.
        """
        with self._call_semaphore:
            first_exc: Exception | None = None
            if self._client is not None:
                try:
                    return fn()
                except Exception as e:
                    if _is_rate_limited(e):
                        time.sleep(_RATE_LIMIT_RETRY_DELAY_SECONDS)
                        try:
                            return fn()
                        except Exception as retry_exc:
                            raise AngelOneAuthError(
                                f"Angel One call still rate-limited after one retry: {retry_exc}"
                            ) from e
                    first_exc = e

            with self._call_lock:
                try:
                    if self._client is None:
                        self.login()
                        return fn()
                except Exception as e:
                    first_exc = first_exc or e
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
        itself. Takes the SAME self._call_lock _call does -- a login
        triggered from here must be mutually exclusive with one
        triggered from a concurrent _call on another thread, or this
        would just reopen the same race _call's own lock closes."""
        with self._call_lock:
            if self._client is None:
                self.login()

    # -- trading ---------------------------------------------------------

    def search_symbol_token(self, exchange: str, query: str) -> list[dict]:
        """exchange e.g. "NSE"; query e.g. "RELIANCE" -- returns whatever
        SmartAPI's own searchScrip finds (each item has at least exchange/
        tradingsymbol/symboltoken), so a caller can resolve a human symbol
        to the symboltoken every other call below needs, without this app
        maintaining its own copy of Angel One's instrument master.

        Returns EVERY matching series for a ticker, not just the primary
        equity -- searchScrip("NSE", "SBIN") returns 14 results (SBIN-AF,
        SBIN-BE, SBIN-BL, SBIN-EQ, SBIN-IQ, SBIN-RL, SBIN-U3, SBIN-U4, plus
        SBINEQWETF-*/SBINMID150-* ETF variants sharing the ticker prefix),
        confirmed directly against a real account. Only SBIN-EQ is the
        actual equity. NEVER take matches[0] from this -- it is not
        guaranteed to be the equity entry (confirmed: for SBIN it would
        resolve to SBIN-AF). Use resolve_equity_symbol below, which is the
        one place this filtering happens; do not re-implement it at a
        call site.
        """
        response = self._call(lambda: self._client.searchScrip(exchange, query))
        if not response or not response.get("status"):
            return []
        return response.get("data") or []

    def resolve_equity_symbol(self, exchange: str, symbol: str) -> dict:
        """The ONLY safe way to turn a human ticker into the symboltoken an
        order should actually route to -- see search_symbol_token's own
        docstring for why matches[0] is a real-money-safety bug (confirmed
        live: it can resolve to an ETF or a different series sharing the
        ticker prefix, not the stock the user meant to trade).

        A bare "-EQ" suffix check is NOT sufficient by itself, and was the
        first (wrong) version of this fix: searchScrip does a SUBSTRING
        search, so searching "SBIN" also returns SBINEQWETF-EQ and
        SBINMID150-EQ (confirmed live) -- two OTHER tickers that happen to
        also end in "-EQ", which would make a suffix-only filter ambiguous
        for exactly the query this is meant to make safe. The filter here
        requires the tradingsymbol be EXACTLY "{symbol}-EQ" (case-
        insensitive), not merely end with "-EQ" -- and the entry's own
        `exchange` field must be NSE (not just the search's own exchange
        param -- searchScrip can return cross-segment matches regardless
        of what exchange was searched). Raises AngelOneError, never falls
        back to an unfiltered guess, when no such entry exists -- a clear
        rejection is the only acceptable failure mode here, not a silent
        wrong-instrument route.

        Caches a successful result by (exchange, symbol) -- see
        self._resolved_symbol_cache's own docstring on why this is safe
        to cache (a static identifier, not a price) and why it matters
        (this is the single biggest real-call reduction available: every
        repeat lookup of a symbol this adapter has already resolved skips
        the network entirely). A FAILED resolution is never cached --
        a transient search failure shouldn't be remembered as permanent.
        """
        cache_key = (exchange, symbol)
        cached = self._resolved_symbol_cache.get(cache_key)
        if cached is not None:
            return cached

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
            # Never silently pick one when the filter itself is ambiguous
            # -- better to fail loudly than to guess with real money.
            raise AngelOneError(
                f"symbol {symbol!r} matched more than one NSE equity entry "
                f"({[m.get('tradingsymbol') for m in equity_matches]}) -- refusing to guess"
            )
        self._resolved_symbol_cache[cache_key] = equity_matches[0]
        return equity_matches[0]

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

    def get_quote_batch(self, exchange_tokens: dict[str, list[str]]) -> dict[str, "Quote"]:
        """Real, batched quotes via getMarketData -- confirmed live,
        2026-09-03, to fetch multiple tokens across DIFFERENT exchange
        segments (e.g. NSE equity + NFO options) in one real call. This
        is what makes an options chain (dozens of strikes) fetchable
        without hammering Angel One's rate limit one contract at a time,
        the same lesson _call_semaphore's own history already
        establishes for equities, just applied here from the start
        instead of learned the hard way a second time.

        MAX_TOKENS_PER_BATCH is not a guess -- confirmed live directly:
        exactly 50 tokens succeeds, 60 fails with Angel One's own real
        "Tokens max limit exceeded" (errorcode AB4029). Splits a larger
        request into multiple real calls transparently; each batch still
        goes through _call, so the same session-refresh/rate-limit
        handling applies to every one of them, not just the first.

        Uses mode="FULL", not "LTP" -- confirmed live this matters, not
        just "more data than needed": last_traded_price for a THINLY
        traded contract can be a real trade from HOURS or a full day
        earlier (confirmed directly: one NIFTY strike's LTP carried
        exchTradeTime from the PREVIOUS session, 34% off its live
        bid/ask midpoint) -- a real, defensible "current price" for an
        options chain needs the live order book, not just whichever
        stale trade happened to be last. best_bid/best_ask come from
        FULL mode's own depth.buy[0]/depth.sell[0] (0.0 when the book's
        side is genuinely empty, not absent -- an illiquid contract with
        no resting interest on one side is a real, distinct state from
        "never quoted at all").

        Returns {token: Quote}. A token Angel One's own response reports
        as "unfetched" (a real, expected outcome -- e.g. a genuinely
        untraded far strike) is simply absent from the result rather
        than raising, since a caller building a chain display needs to
        render "no quote" for ONE strike, not fail the whole chain.
        """
        MAX_TOKENS_PER_BATCH = 50
        result: dict[str, Quote] = {}
        # Flatten to (exchange, token) pairs first so batching splits
        # cleanly across exchange-segment boundaries too -- a single
        # exchange with >50 tokens must not silently get merged into
        # the next exchange's batch.
        flat: list[tuple[str, str]] = [
            (exchange, token) for exchange, tokens in exchange_tokens.items() for token in tokens
        ]
        for i in range(0, len(flat), MAX_TOKENS_PER_BATCH):
            batch = flat[i:i + MAX_TOKENS_PER_BATCH]
            batch_by_exchange: dict[str, list[str]] = {}
            for exchange, token in batch:
                batch_by_exchange.setdefault(exchange, []).append(token)
            response = self._call(lambda be=batch_by_exchange: self._client.getMarketData("FULL", be))
            if not response or not response.get("status"):
                message = (response or {}).get("message", "market data request failed")
                raise AngelOneError(f"Angel One batch quote failed: {message}")
            for row in (response.get("data") or {}).get("fetched") or []:
                depth = row.get("depth") or {}
                buys = depth.get("buy") or []
                sells = depth.get("sell") or []
                # Real levels only -- drop the {price: 0.0, quantity: 0}
                # padding slots Angel One's own response fills unused
                # depth positions with, same sentinel convention
                # best_bid/best_ask below already treat as "no quote".
                bid_levels = [DepthLevel(px=b["price"], qty=int(b.get("quantity") or 0)) for b in buys if b.get("price")]
                ask_levels = [DepthLevel(px=s["price"], qty=int(s.get("quantity") or 0)) for s in sells if s.get("price")]
                result[row["symbolToken"]] = Quote(
                    ltp=row.get("ltp"),
                    close=row.get("close"),
                    best_bid=(buys[0]["price"] if buys and buys[0].get("price") else None),
                    best_ask=(sells[0]["price"] if sells and sells[0].get("price") else None),
                    bids=bid_levels,
                    asks=ask_levels,
                )
        return result


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

    start() does NOT call SmartWebSocketV2.connect() -- it reimplements
    connect()'s own body (header construction, WebSocketApp wiring,
    run_forever) directly, replacing only on_error/on_close. This is a
    real, confirmed bug in the installed smartapi-python 1.5.5 itself, not
    a version pin this app can fix by choosing a different websocket-
    client release: _on_close(self, wsapp) is declared to accept only
    (wsapp), but the installed websocket-client (1.9.0) calls its on_close
    callback with (wsapp, close_status_code, close_msg) -- a real arity
    mismatch confirmed via inspect.signature against the installed
    package, not assumed. Separately, _on_error's own internal retry path
    calls self.on_error("...", "...") (2 args) against the base on_error
    stub's own declared (self) -- zero extra args -- signature; also
    confirmed via inspect.signature. Both raise TypeError from inside
    websocket-client's own callback dispatch, and confirmed LIVE (real
    account, project-5f) to combine with the SDK's own auto-reconnect into
    a storm that hit Angel One's real connection-limit rate limiter
    repeatedly across multiple symbols in a few seconds -- a real-account
    risk, not merely a log spam annoyance. The two safe callbacks below
    accept *args unconditionally (tolerant of whatever arity
    websocket-client actually calls with, now or after either package's
    next release) and do NOT auto-reconnect -- a broken retry loop that
    hammers a broker's rate limiter is worse than no retry at all;
    reconnect policy belongs at a higher level (routers/live_market.py's
    WS endpoint, on an actual client reconnect) with real backoff, not
    inside this wrapper.
    """

    def __init__(self, *, auth_token: str, api_key: str, client_code: str, feed_token: str):
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # lazy -- see module docstring

        self._ws = SmartWebSocketV2(auth_token, api_key, client_code, feed_token)
        self._thread: threading.Thread | None = None

    def start(self, *, on_tick: Callable[[dict], None], on_open: Callable[[], None] | None = None) -> None:
        import logging
        import ssl

        import websocket as websocket_client  # lazy -- see module docstring

        log = logging.getLogger(__name__)
        ws = self._ws
        ws.on_data = lambda _wsapp, data: on_tick(data)
        ws.on_open = (lambda _wsapp: on_open()) if on_open is not None else (lambda _wsapp: None)

        def _safe_on_error(_wsapp, *args) -> None:
            log.warning("Angel One WebSocket error: %s", args)

        def _safe_on_close(_wsapp, *args) -> None:
            log.info("Angel One WebSocket closed: %s", args)

        def _connect() -> None:
            # Mirrors SmartWebSocketV2.connect()'s own header construction
            # exactly (read directly from its installed source) -- only
            # on_error/on_close differ, and only in signature safety, not
            # in what they log.
            headers = {
                "Authorization": ws.auth_token,
                "x-api-key": ws.api_key,
                "x-client-code": ws.client_code,
                "x-feed-token": ws.feed_token,
            }
            ws.wsapp = websocket_client.WebSocketApp(
                ws.ROOT_URI, header=headers, on_open=ws._on_open,
                on_error=_safe_on_error, on_close=_safe_on_close, on_data=ws._on_data,
                on_ping=ws._on_ping, on_pong=ws._on_pong,
            )
            ws.wsapp.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}, ping_interval=ws.HEART_BEAT_INTERVAL)

        self._thread = threading.Thread(target=_connect, daemon=True)
        self._thread.start()

    def subscribe(self, tokens: list[str], correlation_id: str = "algoterminal") -> None:
        self._ws.subscribe(correlation_id, self._ws.LTP_MODE, [{"exchangeType": EXCHANGE_TYPE_NSE_CM, "tokens": tokens}])

    def stop(self) -> None:
        self._ws.close_connection()
