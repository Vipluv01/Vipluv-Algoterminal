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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.markets import NAMED_INSTRUMENTS, MarketRegistry

router = APIRouter()

# symbol -> set of subscribed websockets. Populated/cleaned up per
# connection in the handler below; main.py's tick loop reads this after
# every step_all() to know who to push updates to.
SUBSCRIBERS: dict[str, set[WebSocket]] = {sym: set() for sym in NAMED_INSTRUMENTS}


def _tick_payload(registry: MarketRegistry, symbol: str) -> dict:
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
    if symbol not in NAMED_INSTRUMENTS:
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
