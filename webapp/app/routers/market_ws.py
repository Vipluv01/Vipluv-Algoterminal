"""Live per-symbol market data over WebSocket -- the piece Phase 1's REST
API never needed (order submission/account/dashboard are all
request-response), but the frontend's live-updating chart and order book
do. Same broadcast pattern as sim/bourse_sim/demo_server.py's LiveSim, one
level more general: many symbols, connections subscribe to whichever one
they're currently looking at.
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.markets import DERIVED_INDICES, NAMED_INSTRUMENTS, MarketRegistry

router = APIRouter()

# symbol -> set of subscribed websockets. Populated/cleaned up per
# connection in the handler below; main.py's tick loop reads this after
# every step_all() to know who to push updates to. Covers BOTH real
# instruments and derived indices (NIFTY50/BANKNIFTY) -- a derived index
# has no SymbolMarket/order book of its own (see _tick_payload below), but
# its computed VALUE still streams, the same as GET /symbols already
# treats it as a first-class, if is_derived=True, symbol.
SUBSCRIBERS: dict[str, set[WebSocket]] = {sym: set() for sym in (*NAMED_INSTRUMENTS, *DERIVED_INDICES)}


def _tick_payload(registry: MarketRegistry, symbol: str) -> dict:
    # A derived index (NIFTY50/BANKNIFTY) has no SymbolMarket/Engine of its
    # own -- registry[symbol] would KeyError -- so it has no real best_bid/
    # best_ask/depth to report either. Streaming its computed price is
    # honest; fabricating a synthetic order book for it would not be, so
    # those fields go through as None/empty rather than invented.
    # Server-stamped at the moment this payload is BUILT (immediately
    # before it's serialized and sent), in epoch milliseconds -- matching
    # /market/history's own timestamp convention. The client computes its
    # own delivery delta (Date.now() - sent_at) rather than this server
    # trying to guess a network/queueing delay it cannot see; see
    # app.telemetry's own docstring on measuring the real thing, not a
    # borrowed or estimated one.
    sent_at = int(time.time() * 1000)

    if symbol in DERIVED_INDICES:
        return {
            "type": "tick",
            "symbol": symbol,
            "price": registry.current_prices()[symbol],
            "best_bid": None,
            "best_ask": None,
            "bids": [],
            "asks": [],
            "sent_at": sent_at,
        }

    # Engine.best_bid()/best_ask()/depth() all return prices in integer
    # ticks, not currency -- every one needs *market.tick_size before it
    # means anything to a UI. (market.current_price is already converted,
    # since SymbolMarket.step() does that conversion itself before
    # appending to price_history.)
    market = registry[symbol]
    tick_size = market.tick_size
    bid = market.eng.best_bid()
    ask = market.eng.best_ask()
    bids, asks = market.eng.depth(10)
    return {
        "type": "tick",
        "symbol": symbol,
        "price": market.current_price,
        "best_bid": bid[0] * tick_size if bid else None,
        "best_ask": ask[0] * tick_size if ask else None,
        "bids": [{"px": lvl.px * tick_size, "qty": lvl.qty} for lvl in bids],
        "asks": [{"px": lvl.px * tick_size, "qty": lvl.qty} for lvl in asks],
        "sent_at": sent_at,
    }


async def broadcast_ticks(registry: MarketRegistry) -> None:
    """Called once per market tick (see app/main.py) -- pushes the latest
    price/depth to every connection currently subscribed to each symbol.
    A symbol with no subscribers costs nothing beyond building its own
    payload dict, which is cheap relative to the tick itself."""
    for symbol, subs in SUBSCRIBERS.items():
        if not subs:
            continue
        payload = json.dumps(_tick_payload(registry, symbol))
        await asyncio.gather(
            *(_safe_send(ws, payload) for ws in list(subs)), return_exceptions=True,
        )


async def _safe_send(ws: WebSocket, payload: str) -> None:
    try:
        await ws.send_text(payload)
    except Exception:
        pass  # connection is being torn down elsewhere; the disconnect handler below cleans up SUBSCRIBERS


@router.websocket("/ws/market/{symbol}")
async def market_ws(websocket: WebSocket, symbol: str):
    if symbol not in NAMED_INSTRUMENTS and symbol not in DERIVED_INDICES:
        await websocket.close(code=4404, reason=f"unknown symbol {symbol!r}")
        return

    await websocket.accept()
    SUBSCRIBERS[symbol].add(websocket)
    try:
        # Send one immediate snapshot on connect, rather than making a new
        # subscriber wait up to a full tick interval for its first paint.
        registry: MarketRegistry = websocket.app.state.registry
        await websocket.send_text(json.dumps(_tick_payload(registry, symbol)))
        while True:
            # This connection is receive-only from the client's side (no
            # commands yet, unlike the bourse demo's pause/step/order
            # controls -- trading goes through the REST /orders endpoint
            # instead). Still need to await something so a client
            # disconnect is detected promptly rather than only on the next
            # failed send.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        SUBSCRIBERS[symbol].discard(websocket)
