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

UNTESTED against a real Angel One feed (no live account exists to test
against) -- see app/broker/angelone.py's own docstring on which parts of
the underlying contract are confirmed from source vs. community-sourced.
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
from app.broker.angelone import AngelOneError, AngelOneLiveFeed
from app.db import get_db
from app.models.user import User

router = APIRouter(prefix="/live", tags=["live"])

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
    try:
        matches = adapter.search_symbol_token("NSE", symbol)
    except AngelOneError as e:
        raise HTTPException(status_code=502, detail=f"Angel One symbol lookup failed: {e}")
    if not matches:
        raise HTTPException(status_code=404, detail=f"unknown symbol {symbol!r} on NSE")
    return matches[0]["symboltoken"]


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
    from_dt = to_dt - timedelta(seconds=INTERVAL_SECONDS[interval] * limit)
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
    await websocket.accept()

    try:
        adapter = get_adapter_for_user(db, user.id)
    except (NoBrokerCredentialError, IncompleteBrokerCredentialError) as e:
        await websocket.close(code=4400, reason=str(e))
        return

    try:
        symboltoken = adapter.search_symbol_token("NSE", symbol)
        if not symboltoken:
            await websocket.close(code=4404, reason=f"unknown symbol {symbol!r}")
            return
        token = symboltoken[0]["symboltoken"]
        adapter.ensure_session()  # AngelOneLiveFeed needs a live jwt/feed token directly, before its first use
    except AngelOneError as e:
        await websocket.close(code=4500, reason=str(e))
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def on_tick(data: dict) -> None:
        # Called from AngelOneLiveFeed's own background thread (see its
        # own docstring) -- call_soon_threadsafe is the one safe way to
        # hand data back to this coroutine's event loop from there.
        loop.call_soon_threadsafe(queue.put_nowait, data)

    feed = AngelOneLiveFeed(
        auth_token=adapter._jwt_token, api_key=adapter._creds.api_key,
        client_code=adapter._creds.client_code, feed_token=adapter._feed_token,
    )
    feed.start(on_tick=on_tick)
    feed.subscribe([token])

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
        feed.stop()
