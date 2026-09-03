"""Live-mode historical bars + live tick feed, backed by Angel One's real
candle API and WebSocket feed -- routers/market.py and market_ws.py stay
completely untouched (paper AND virtual both keep using the simulated
engine's own price_history, per Mode.virtual's own docstring); this is
purely additive, a separate `/live` prefix so a frontend chart can choose
its data source by which endpoint it calls rather than by a mode branch
inside one shared endpoint.

Response shapes deliberately mirror market.py's HistoryOut/BarOut and
market_ws.py's tick payload field-for-field where a real equivalent
exists, specifically so a chart consumer built against paper/virtual mode
needs to change its data SOURCE, not its data SHAPE, to point at live
mode. Two honest gaps where there's no simulated-engine equivalent to
mirror: a live LTP-mode tick has no real order-book depth (best_bid/
best_ask/bids/asks go through as None/[], the same "nothing fabricated"
convention market_ws.py already uses for a derived index's own missing
book), and Angel One's candle API only offers whole-minute-and-up
intervals (no 1s/5s bars the way the simulated engine's per-second
price_history allows).

The WS endpoint acquires its feed through app/broker/feed_registry.py,
not a per-connection AngelOneLiveFeed -- see that module's own docstring
on why: a real incident (Aug 28-29), where a per-connection feed
combined with an uncapped client-side retry loop produced 1,539
reconnect attempts against the real account over ~4 hours, hitting Angel
One's own connection-rate limiter repeatedly. feed_registry shares one
real upstream connection across every subscriber to the same symbol and
enforces a real cooldown on how often a new one can be created, as a
server-side backstop independent of whether the client behaves.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.broker.adapter_cache import IncompleteBrokerCredentialError, NoBrokerCredentialError, get_adapter_for_user
from app.broker.angelone import AngelOneError
from app.broker.feed_registry import FeedCreationThrottled, acquire_feed, release_feed
from app.broker.instrument_master import list_live_equity_names
from app.db import get_db
from app.models.user import User

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/market/equities", response_model=list[str])
def get_live_equities():
    """Every real NSE equity Angel One's own instrument master currently
    lists (~2000+) -- NOT this app's own 7-symbol NAMED_INSTRUMENTS
    (app/markets.py), which paper/virtual's simulated engine is fixed
    to. A local instrument-master lookup, no broker credential or real
    Angel One call needed (same reasoning as GET /live/options/
    underlyings' own docstring). This is what lets a live-mode symbol
    picker offer any real stock, not just the handful the simulated
    engine also happens to model -- the live history/quote/WS endpoints
    below already accept an arbitrary resolvable symbol string; the only
    thing that was ever narrower was the frontend's own picker source
    (GET /symbols, paper-oriented)."""
    return list_live_equity_names()

# Angel One's getCandleData only offers whole-minute-and-up granularity
# (see angelone.py's own CANDLE_INTERVALS) -- there is no live-mode
# equivalent of market.py's 1s/5s bars, which only exist there because
# the simulated engine's price_history is itself per-second.
INTERVAL_SECONDS: dict[str, int] = {"1m": 60, "15m": 900, "1hr": 3600, "1d": 86400}

MAX_BARS = 1000
DEFAULT_BARS = 200


class BarOut(BaseModel):
    timestamp: int  # milliseconds -- matches market.py's BarOut convention
    open: float
    high: float
    low: float
    close: float
    volume: int | None


class HistoryOut(BaseModel):
    symbol: str
    interval: str
    requested_bars: int
    returned_bars: int
    bars: list[BarOut]


def _get_adapter_or_400(db: Session, user_id: int):
    try:
        return get_adapter_for_user(db, user_id)
    except (NoBrokerCredentialError, IncompleteBrokerCredentialError) as e:
        raise HTTPException(status_code=400, detail=str(e))


def _resolve_symboltoken(adapter, symbol: str) -> str:
    # AngelOneError caught HERE (not left to each call site) -- a real
    # bug shipped without this: search_symbol_token can raise on login
    # failure alone (bad/expired credentials), and an uncaught
    # AngelOneError is just as much an unhandled-500 to FastAPI as any
    # other exception type -- only HTTPException gets turned into a
    # clean response automatically. Caught directly against a live dev
    # server (bourse1, testing with a deliberately invalid TOTP secret).
    #
    # resolve_equity_symbol, not search_symbol_token + matches[0] -- the
    # same real-money-safety bug found live in orders.py's confirm
    # endpoint applies here too: a chart/history request for "SBIN" must
    # resolve to the actual equity (SBIN-EQ), not silently show an ETF or
    # a different series sharing the ticker prefix. See
    # AngelOneAdapter.resolve_equity_symbol's own docstring.
    try:
        match = adapter.resolve_equity_symbol("NSE", symbol)
    except AngelOneError as e:
        raise HTTPException(status_code=502, detail=f"Angel One symbol lookup failed: {e}")
    return match["symboltoken"]


@router.get("/market/history", response_model=HistoryOut)
def get_live_history(
    symbol: str,
    interval: Literal["1m", "15m", "1hr", "1d"] = "1m",
    limit: int = Query(DEFAULT_BARS, gt=0, le=MAX_BARS),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    adapter = _get_adapter_or_400(db, user.id)
    symboltoken = _resolve_symboltoken(adapter, symbol)

    to_dt = datetime.now()
    # `limit * interval` alone is anchored to WALL-CLOCK now(), not to the
    # market's actual last trading session -- confirmed live, 2026-09-03
    # 21:12 IST: a 1m/300-bar request (a 5-hour natural lookback) against a
    # real, actively-traded symbol (ICICIBANK) returned ZERO bars, because
    # NSE closed at 15:30 and the entire 5-hour window fell after that.
    # Angel One's candle API only returns bars for minutes that actually
    # traded (confirmed by that same empty response, not padded with
    # closed-market filler), so widening the request window is safe --
    # it can only pick up MORE real bars, never fabricate any -- and the
    # `[-limit:]` slice below still caps the response at what was asked
    # for. 10 calendar days floors even an extended holiday weekend.
    lookback = max(timedelta(seconds=INTERVAL_SECONDS[interval] * limit), timedelta(days=10))
    from_dt = to_dt - lookback
    try:
        raw_bars = adapter.get_historical_candles(
            exchange="NSE", symboltoken=symboltoken, interval=interval, from_dt=from_dt, to_dt=to_dt,
        )
    except AngelOneError as e:
        raise HTTPException(status_code=502, detail=f"Angel One historical candles failed: {e}")

    bars = [
        BarOut(timestamp=b["timestamp_ms"], open=b["open"], high=b["high"], low=b["low"],
               close=b["close"], volume=b["volume"])
        for b in raw_bars[-limit:]
    ]
    return HistoryOut(symbol=symbol, interval=interval, requested_bars=limit, returned_bars=len(bars), bars=bars)


class DepthLevelOut(BaseModel):
    px: float
    qty: int


class QuoteOut(BaseModel):
    symbol: str
    ltp: float | None
    close: float | None  # real previous close from Angel One's own quote -- None if unresolvable/unquoted
    # Real multi-level order-book depth (Quote.bids/asks, FULL mode's own
    # depth.buy/depth.sell) -- the SAME real data the options chain
    # already uses, now also on the equity quote so a live-mode order
    # book can show real resting depth instead of the WS LTP feed's own
    # empty bids/asks (see live_market_ws's own _live_tick_to_payload,
    # unchanged -- that feed genuinely carries no depth; this is a
    # separate, REST-polled source for it). Empty list, not missing/None,
    # when a side is genuinely unquoted.
    bids: list[DepthLevelOut]
    asks: list[DepthLevelOut]


class QuotesOut(BaseModel):
    quotes: list[QuoteOut]


@router.get("/market/quotes", response_model=QuotesOut)
def get_live_quotes(
    symbols: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Real LTP + real previous close, batched -- what Ticker.js's daily
    %-change display actually needs and GET /market/history (bars only,
    no prev-close field) can't provide. This was the real bug behind a
    live-mode ticker showing swings like -55%/+44%: with nothing else to
    compare against, the frontend was computing %-change against
    NAMED_INSTRUMENTS' static simulated seed price (app/markets.py's own
    "illustrative... not a live quote" reference for PAPER mode) instead
    of any real baseline -- comparing a real live price against an
    unrelated constant from a different mode entirely. This endpoint
    gives live mode its own real reference (Quote.close, from Angel
    One's real getMarketData FULL-mode response) so %-change is
    LTP-vs-real-previous-close, the same thing every real trading
    screen means by "daily change."

    `symbols` is comma-separated (not repeated query params) to keep
    this a single request for the whole ticker strip, not one per
    symbol -- exactly the batching lesson get_quote_batch's own
    MAX_TOKENS_PER_BATCH history already established for the options
    chain. One symbol failing to resolve (TATAMOTORS) must not blank the
    other 6 real ones -- each symbol's own resolution is caught
    independently, never propagated as a whole-request 502.
    """
    adapter = _get_adapter_or_400(db, user.id)
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    token_to_symbol: dict[str, str] = {}
    unresolved: list[str] = []
    for sym in symbol_list:
        try:
            match = adapter.resolve_equity_symbol("NSE", sym)
        except AngelOneError:
            unresolved.append(sym)
            continue
        token_to_symbol[match["symboltoken"]] = sym

    quote_by_token = {}
    if token_to_symbol:
        try:
            quote_by_token = adapter.get_quote_batch({"NSE": list(token_to_symbol)})
        except AngelOneError as e:
            raise HTTPException(status_code=502, detail=f"Angel One quote fetch failed: {e}")

    quotes = []
    for token, sym in token_to_symbol.items():
        q = quote_by_token.get(token)
        quotes.append(QuoteOut(
            symbol=sym, ltp=q.ltp if q else None, close=q.close if q else None,
            bids=[DepthLevelOut(px=lvl.px, qty=lvl.qty) for lvl in q.bids] if q else [],
            asks=[DepthLevelOut(px=lvl.px, qty=lvl.qty) for lvl in q.asks] if q else [],
        ))
    for sym in unresolved:
        quotes.append(QuoteOut(symbol=sym, ltp=None, close=None, bids=[], asks=[]))
    return QuotesOut(quotes=quotes)


def _live_tick_to_payload(symbol: str, tick: dict) -> dict:
    """SmartWebSocketV2's own parsed tick dict (angelone.py's own
    docstring: field names confirmed from SDK source) -> the same tick
    shape market_ws.py's own _tick_payload emits. last_traded_price's
    paise-vs-rupee scaling is NOT independently confirmed (see
    angelone.py's docstring) -- divide by 100 here is the commonly
    documented convention, flagged the same way there."""
    ltp_raw = tick.get("last_traded_price")
    price = (ltp_raw / 100.0) if ltp_raw is not None else None
    return {
        "type": "tick",
        "symbol": symbol,
        "price": price,
        "best_bid": None,
        "best_ask": None,
        "bids": [],
        "asks": [],
        "sent_at": int(time.time() * 1000),
    }


@router.websocket("/ws/market/{symbol}")
async def live_market_ws(
    websocket: WebSocket, symbol: str, user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Subscribes to the SHARED feed_registry feed for (user.id, symbol),
    not a per-connection AngelOneLiveFeed -- see feed_registry.py's own
    docstring on the real incident (a 4-hour storm against the real
    account's rate limiter) this fixes. Every branch that can return
    before subscribing must not have registered anything with
    feed_registry yet; every branch that DOES subscribe must release in
    `finally`, unconditionally -- an unreleased subscriber_id is a shared
    feed that never gets torn down.
    """
    await websocket.accept()

    try:
        adapter = get_adapter_for_user(db, user.id)
    except (NoBrokerCredentialError, IncompleteBrokerCredentialError) as e:
        await websocket.close(code=4400, reason=str(e))
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_tick(data: dict) -> None:
        # Called from feed_registry's fan-out, itself called from
        # AngelOneLiveFeed's own background thread (see its own
        # docstring) -- call_soon_threadsafe is the one safe way to hand
        # data back to THIS connection's event loop from there.
        loop.call_soon_threadsafe(queue.put_nowait, data)

    def _connect() -> None:
        # Runs OFF the event loop thread via asyncio.to_thread below --
        # every call here is real, synchronous network I/O (resolve_
        # equity_symbol and ensure_session both funnel through
        # AngelOneAdapter._call, which holds _call_lock -- a plain
        # threading.Lock -- for the full round trip; acquire_feed's own
        # first-subscriber path does a real WebSocket handshake too).
        # This function used to run directly in this route's own async
        # body with no offload -- a real, confirmed incident: opening one
        # live-mode chart blocked the ENTIRE asyncio event loop for the
        # duration of that Angel One call, freezing every other request
        # in the process (a plain POST /orders submission, which never
        # touches the broker, hung indefinitely while a live WS
        # connection was mid-resolve). Same lesson app/broker/notify.py's
        # own docstring already describes -- this route just hadn't
        # applied it. `to_thread` puts this on a real OS thread instead,
        # so the lock/network wait blocks that thread, never the loop.
        #
        # resolve_equity_symbol, not search_symbol_token + matches[0] --
        # see AngelOneAdapter.resolve_equity_symbol's own docstring on the
        # real-money/correctness bug this replaces (matches[0] is not
        # guaranteed to be the actual equity series).
        match = adapter.resolve_equity_symbol("NSE", symbol)
        token = match["symboltoken"]
        adapter.ensure_session()  # AngelOneLiveFeed needs a live jwt/feed token directly, before its first use
        acquire_feed(
            user_id=user.id, symbol=symbol, adapter=adapter, symboltoken=token,
            subscriber_id=websocket, on_tick=on_tick,
        )

    try:
        await asyncio.to_thread(_connect)
    except AngelOneError as e:
        await websocket.close(code=4404, reason=str(e))
        return
    except FeedCreationThrottled as e:
        # A real, deliberate rejection -- NOT something this endpoint
        # retries on the caller's behalf (that would just reintroduce the
        # retry-storm risk this whole registry exists to close off). The
        # client sees a clean close code and decides its own backoff.
        await websocket.close(code=4429, reason=str(e))
        return

    try:
        # Two concurrent waits, same reason market_ws.py's own receive
        # loop exists: without also watching for an incoming client
        # message (browsers send none, but a disconnect surfaces as one
        # completing/raising), a client going away wouldn't be noticed
        # until the next tick happened to be sent into a dead socket.
        recv_task = asyncio.ensure_future(websocket.receive_text())
        while True:
            get_task = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait({recv_task, get_task}, return_when=asyncio.FIRST_COMPLETED)
            if recv_task in done:
                recv_task.result()  # raises WebSocketDisconnect on a real disconnect
                recv_task = asyncio.ensure_future(websocket.receive_text())
            if get_task in done:
                tick = get_task.result()
                await websocket.send_text(json.dumps(_live_tick_to_payload(symbol, tick)))
            else:
                get_task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        release_feed(user_id=user.id, symbol=symbol, subscriber_id=websocket)
