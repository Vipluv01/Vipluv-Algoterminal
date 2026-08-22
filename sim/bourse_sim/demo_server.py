"""Live WebSocket server driving the real simulation, for the browser demo.

This is deliberately NOT a rewrite of simulate.py for interactivity -- it
reuses the exact same agents (NoiseTrader, InformedTrader, MarketMaker) and
the exact same Engine subprocess bridge that produced every validated result
in this project. The only new thing here is the loop shape: run_simulation
does a fixed number of steps and returns an array at the end; this runs
indefinitely, broadcasting state after every step, and accepts live control
messages (pause, step, spawn a trader, change volatility) from connected
browsers. Two different consumption patterns of the same simulation
primitives, not two different simulations.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import websockets
from websockets.http11 import Response
from websockets.datastructures import Headers

from agents import InformedTrader, MarketMaker, NoiseTrader
from avellaneda_stoikov import AvellanedaStoikovMaker, AvellanedaStoikovParams
from engine import Engine
from fundamental import FundamentalProcess
from simulate import owner_of_order_id, to_ticks_static

# Cap on live-spawned informed traders: this server is public once deployed,
# and add_informed_trader() is reachable by ANY connected browser tab, not
# just a trusted operator -- an uncapped loop of that command would grow
# per-step work without bound and degrade the simulation for everyone else
# watching. 50 is far more than the demo's own UI would ever spawn by hand.
MAX_INFORMED_TRADERS = 50

# Same public-abuse reasoning applies to a human's own order flow: without a
# ceiling, one visitor could submit orders in a tight client-side loop and
# either flood the book with resting orders or force the server to do
# unbounded per-step work, degrading the shared live market for everyone
# else watching the same session.
MAX_ORDER_QTY = 5000
MAX_OPEN_ORDERS_PER_HUMAN = 20

# Human trader ids live in their own reserved range, well clear of the bots
# (noise=100-119, informed=200+, maker=1) and the seed-liquidity owner
# (999) -- a collision would misattribute fills via owner_of_order_id.
FIRST_HUMAN_TRADER_ID = 10_000

INDEX_HTML_PATH = Path(__file__).resolve().parents[1] / "demo" / "index.html"


@dataclass
class HumanTrader:
    """A visiting browser's own trading identity in the shared live market.

    Deliberately NOT a variant of agents.py's MarketMaker/NoiseTrader --
    those are validated SIMULATION agents whose behavior feeds the project's
    actual research results, and reusing them here (even by subclassing)
    would risk this purely-interactive, unvalidated code path leaking into
    that. inventory/cash/on_fill/mark_to_market intentionally mirror
    MarketMaker's own fields (see agents.py) because it's the same simple,
    already-correct accounting -- fills move inventory and cash in opposite
    directions, mark-to-market is cash plus inventory priced at the current
    mid -- not because the two classes are meant to be interchangeable.
    """

    trader_id: int
    tick_size: float
    inventory: int = field(default=0, init=False)
    cash: float = field(default=0.0, init=False)
    # order_id -> {"side", "px", "qty"} for this human's own RESTING orders
    # only -- immediately-filled quantity never enters this dict. Tracked
    # here, not queried from the engine, because there's no wire op to list
    # orders by owner; the demo server is the only writer of this human's
    # orders, so it can just keep its own ledger in sync as it goes.
    open_orders: dict = field(default_factory=dict, init=False)
    _seq: int = field(default=0, init=False)

    def next_order_id(self) -> int:
        self._seq += 1
        return self.trader_id * 10_000_000 + self._seq

    def on_fill(self, side: str, qty: int, px_ticks: int) -> None:
        if side == "buy":
            self.inventory += qty
            self.cash -= qty * px_ticks
        else:
            self.inventory -= qty
            self.cash += qty * px_ticks

    def mark_to_market(self, mid_ticks: int) -> float:
        return (self.cash + self.inventory * mid_ticks) * self.tick_size

    def reduce_open_order(self, order_id: int, qty: int) -> None:
        entry = self.open_orders.get(order_id)
        if entry is None:
            return
        entry["qty"] -= qty
        if entry["qty"] <= 0:
            del self.open_orders[order_id]


@dataclass
class LiveSim:
    tick_size: float = 0.01
    s0: float = 100.0
    fundamental_sigma: float = 0.25
    seed: int = 0

    paused: bool = field(default=False, init=False)
    step_count: int = field(default=0, init=False)
    speed_ms: int = field(default=150, init=False)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self.min_px = to_ticks_static(self.s0 * 0.5, self.tick_size)
        self.max_px = to_ticks_static(self.s0 * 2.0, self.tick_size)
        self.fundamental = FundamentalProcess(s0=self.s0, sigma=self.fundamental_sigma, seed=self.seed)

        self.noise_traders = [
            NoiseTrader(trader_id=100 + i, tick_size=self.tick_size,
                        rng=np.random.default_rng(self.rng.integers(0, 2**31)))
            for i in range(20)
        ]
        self.informed_traders = [
            InformedTrader(trader_id=200 + i, tick_size=self.tick_size,
                            rng=np.random.default_rng(self.rng.integers(0, 2**31)))
            for i in range(5)
        ]
        self.maker = MarketMaker(trader_id=1, tick_size=self.tick_size, quote_size=100)
        self.maker_ids = {self.maker.trader_id}
        self._next_informed_id = 300
        self.humans_by_id: dict[int, HumanTrader] = {}
        self._next_human_trader_id = FIRST_HUMAN_TRADER_ID

        self.eng = Engine(min_px=self.min_px, max_px=self.max_px, tick=1, capacity=1 << 18)
        start_ticks = to_ticks_static(self.s0, self.tick_size)
        self.eng.submit(order_id=999_000_001, side="buy", qty=30, px=start_ticks - 5, owner=999)
        self.eng.submit(order_id=999_000_002, side="sell", qty=30, px=start_ticks + 5, owner=999)
        self.last_known_mid_ticks = start_ticks
        self.last_mid: float | None = None
        self.recent_returns: list[float] = []
        self.recent_trades: list[dict] = []  # rolling window for the tape/chart

    def close(self) -> None:
        self.eng.close()

    def add_informed_trader(self) -> None:
        if len(self.informed_traders) >= MAX_INFORMED_TRADERS:
            return
        self._next_informed_id += 1
        self.informed_traders.append(
            InformedTrader(trader_id=self._next_informed_id, tick_size=self.tick_size,
                            rng=np.random.default_rng(self.rng.integers(0, 2**31)))
        )

    def set_volatility(self, sigma: float) -> None:
        self.fundamental.sigma = max(0.01, min(2.0, sigma))

    def register_human(self) -> HumanTrader:
        self._next_human_trader_id += 1
        h = HumanTrader(trader_id=self._next_human_trader_id, tick_size=self.tick_size)
        self.humans_by_id[h.trader_id] = h
        return h

    def unregister_human(self, human: HumanTrader) -> None:
        # Cancel first: an orphaned resting order from a visitor who closed
        # the tab would otherwise sit on the book indefinitely as bogus
        # liquidity nobody can ever manage, silently distorting the shared
        # market everyone else is still watching.
        for order_id in list(human.open_orders.keys()):
            self.eng.cancel(order_id)
        human.open_orders.clear()
        self.humans_by_id.pop(human.trader_id, None)

    def submit_human_order(self, human: HumanTrader, *, side, qty, px, order_type) -> dict:
        if side not in ("buy", "sell"):
            return {"ok": False, "error": "side must be 'buy' or 'sell'"}
        if order_type not in ("limit", "market"):
            return {"ok": False, "error": "order_type must be 'limit' or 'market'"}
        if not isinstance(qty, int) or not (0 < qty <= MAX_ORDER_QTY):
            return {"ok": False, "error": f"qty must be a whole number between 1 and {MAX_ORDER_QTY}"}

        px_ticks = 0
        if order_type == "limit":
            if px is None or px <= 0:
                return {"ok": False, "error": "limit orders need a positive price"}
            if len(human.open_orders) >= MAX_OPEN_ORDERS_PER_HUMAN:
                return {"ok": False, "error": f"too many open orders (max {MAX_OPEN_ORDERS_PER_HUMAN}) -- cancel one first"}
            px_ticks = to_ticks_static(px, self.tick_size)

        order_id = human.next_order_id()
        result = self.eng.submit(order_id=order_id, side=side, qty=qty, px=px_ticks,
                                  owner=human.trader_id, order_type=order_type, tif="gtc")
        new_trades = self._route(result)

        if result.accepted and order_type == "limit":
            resting = qty - result.filled_qty
            if resting > 0:
                human.open_orders[order_id] = {"side": side, "px": px, "qty": resting}

        return {
            "ok": result.accepted, "reject": result.reject, "order_id": order_id,
            "filled_qty": result.filled_qty, "trades": new_trades, "order_type": order_type,
        }

    def cancel_human_order(self, human: HumanTrader, order_id: int) -> dict:
        if order_id not in human.open_orders:
            return {"ok": False, "error": "not your open order (already filled or cancelled)"}
        reject = self.eng.cancel(order_id)
        if reject != "none":
            return {"ok": False, "error": reject}
        del human.open_orders[order_id]
        return {"ok": True, "order_id": order_id}

    def _route(self, result) -> list[dict]:
        new_trades = []
        if result is None:
            return new_trades
        for f in result.fills:
            new_trades.append({"px": f.px * self.tick_size, "qty": f.qty, "side": f.taker_side})
            mo, to_ = owner_of_order_id(f.maker_id), owner_of_order_id(f.taker_id)
            if mo in self.maker_ids:
                self.maker.on_fill("sell" if f.taker_side == "buy" else "buy", f.qty, f.px)
            elif to_ in self.maker_ids:
                self.maker.on_fill(f.taker_side, f.qty, f.px)
            maker_human = self.humans_by_id.get(mo)
            if maker_human is not None:
                maker_human.on_fill("sell" if f.taker_side == "buy" else "buy", f.qty, f.px)
                maker_human.reduce_open_order(f.maker_id, f.qty)
            taker_human = self.humans_by_id.get(to_)
            if taker_human is not None:
                taker_human.on_fill(f.taker_side, f.qty, f.px)
        return new_trades

    def step(self) -> dict:
        self.fundamental.step()
        mid = self.eng.mid()
        if mid is not None:
            self.last_known_mid_ticks = int(round(mid))
        mid_ticks = self.last_known_mid_ticks
        bid = self.eng.best_bid()
        ask = self.eng.best_ask()
        spread_ticks = (ask[0] - bid[0]) if (bid and ask) else 4

        vol_estimate = float(np.std(self.recent_returns[-50:])) if len(self.recent_returns) >= 10 else 0.0
        self.maker.refresh_quotes(self.eng, mid_ticks, vol_estimate)

        new_trades: list[dict] = []
        for nt in self.noise_traders:
            if self.rng.random() < 0.3:
                new_trades += self._route(nt.act(self.eng, mid_ticks, spread_ticks))
        for it in self.informed_traders:
            if self.rng.random() < 0.5:
                new_trades += self._route(it.act(self.eng, self.fundamental.value, mid_ticks if bid and ask else None))

        new_mid = self.eng.mid()
        if new_mid is not None:
            if self.last_mid is not None and self.last_mid > 0:
                self.recent_returns.append(np.log(new_mid / self.last_mid))
            self.last_mid = new_mid
            self.last_known_mid_ticks = int(round(new_mid))
        mark_mid = self.last_known_mid_ticks * self.tick_size

        self.step_count += 1
        bids, asks = self.eng.depth(8)

        return {
            "type": "tick",
            "step": self.step_count,
            "mid": mark_mid,
            "fundamental": self.fundamental.value,
            "bids": [{"px": l.px * self.tick_size, "qty": l.qty} for l in bids],
            "asks": [{"px": l.px * self.tick_size, "qty": l.qty} for l in asks],
            "trades": new_trades,
            "maker": {
                "inventory": self.maker.inventory,
                "pnl": self.maker.mark_to_market(self.last_known_mid_ticks),
                "spread_ticks": self.maker.base_half_spread_ticks * 2,
            },
        }


CLIENTS: set = set()
# ws -> HumanTrader, mirrored 1:1 with CLIENTS -- kept as a separate dict
# (rather than folded into LiveSim, which is recreated fresh by sim_loop)
# so a connection's identity survives independently of simulation state.
HUMANS_BY_WS: dict = {}
SIM: LiveSim | None = None


async def broadcast(msg: dict) -> None:
    if not CLIENTS:
        return
    data = json.dumps(msg)
    await asyncio.gather(*(c.send(data) for c in list(CLIENTS)), return_exceptions=True)


async def send_human_states() -> None:
    """Per-client personal state (position/P&L/open orders) -- deliberately
    NOT folded into the shared broadcast() message, since every other
    client's browser would otherwise receive (and have to ignore) every
    other visitor's private trading state on every single tick."""
    if not HUMANS_BY_WS or SIM is None:
        return
    mid_ticks = SIM.last_known_mid_ticks

    async def _send(ws, human: HumanTrader) -> None:
        payload = json.dumps({
            "type": "your_state",
            "position": human.inventory,
            "pnl": human.mark_to_market(mid_ticks),
            "open_orders": [{"order_id": oid, **info} for oid, info in human.open_orders.items()],
        })
        try:
            await ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            pass

    await asyncio.gather(*(_send(ws, h) for ws, h in list(HUMANS_BY_WS.items())), return_exceptions=True)


async def sim_loop() -> None:
    global SIM
    SIM = LiveSim()
    try:
        while True:
            if not SIM.paused:
                msg = SIM.step()
                await broadcast(msg)
                await send_human_states()
            await asyncio.sleep(SIM.speed_ms / 1000.0)
    finally:
        SIM.close()


def _parse_order_cmd(cmd: dict) -> dict | None:
    """Turns raw client JSON into the kwargs submit_human_order expects, or
    None if it's malformed -- isolated from the dispatch loop below so a
    bad qty/px from a client (wrong type, missing field, garbage string)
    can't take the whole connection handler down with an uncaught
    ValueError/TypeError, the same defensive-parsing discipline the Go wire
    protocol already applies to every field it reads from a request."""
    try:
        side = cmd.get("side")
        order_type = cmd.get("order_type", "limit")
        qty = int(cmd.get("qty"))
        raw_px = cmd.get("px")
        px = float(raw_px) if raw_px not in (None, "") else None
        return {"side": side, "qty": qty, "px": px, "order_type": order_type}
    except (TypeError, ValueError):
        return None


async def handle_client(ws) -> None:
    CLIENTS.add(ws)
    human = SIM.register_human() if SIM is not None else None
    if human is not None:
        HUMANS_BY_WS[ws] = human
        await ws.send(json.dumps({"type": "welcome", "trader_id": human.trader_id}))
    try:
        async for raw in ws:
            if SIM is None:
                continue
            try:
                cmd = json.loads(raw)
            except json.JSONDecodeError:
                continue
            c = cmd.get("cmd")
            if c == "pause":
                SIM.paused = True
            elif c == "resume":
                SIM.paused = False
            elif c == "step":
                m = SIM.step()
                await broadcast(m)
                await send_human_states()
            elif c == "add_informed":
                SIM.add_informed_trader()
            elif c == "set_volatility":
                SIM.set_volatility(float(cmd.get("value", 0.25)))
            elif c == "set_speed":
                SIM.speed_ms = max(10, int(cmd.get("value", 150)))
            elif c == "submit_order" and human is not None:
                parsed = _parse_order_cmd(cmd)
                result = (SIM.submit_human_order(human, **parsed) if parsed is not None
                          else {"ok": False, "error": "malformed order"})
                await ws.send(json.dumps({"type": "order_result", **result}))
                await send_human_states()
            elif c == "cancel_order" and human is not None:
                try:
                    order_id = int(cmd.get("order_id"))
                except (TypeError, ValueError):
                    result = {"ok": False, "error": "malformed order_id"}
                else:
                    result = SIM.cancel_human_order(human, order_id)
                await ws.send(json.dumps({"type": "order_result", **result}))
                await send_human_states()
    finally:
        CLIENTS.discard(ws)
        HUMANS_BY_WS.pop(ws, None)
        if human is not None and SIM is not None:
            SIM.unregister_human(human)


_INDEX_HTML_BYTES = INDEX_HTML_PATH.read_bytes()


async def serve_static_or_upgrade(connection, request):
    """process_request hook: serves the demo's one static file for a plain
    GET, and returns None (the websockets default) for everything else --
    including the actual WebSocket upgrade request, which MUST fall through
    to the library's own handshake handling to work at all.

    This is what lets one process, one port, serve both the frontend and
    the live simulation feed -- required for deploying anywhere that only
    routes a single public port to a service (which is most free hosting
    tiers), and simpler than reasoning about two ports staying in sync
    between local dev and production.
    """
    # A real WebSocket handshake also arrives as a GET to "/" -- the ONLY
    # thing distinguishing it from a plain page load is the Upgrade header.
    # Matching on path alone (an earlier version of this) served the raw
    # HTML back as the response to the handshake itself, which the browser
    # correctly rejects as a failed upgrade -- silently breaking the entire
    # live feed while the page still loaded fine, which is exactly the kind
    # of bug that looks like "it's working" until you check the console.
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    if request.path in ("/", "/index.html"):
        return Response(200, "OK", Headers([("Content-Type", "text/html; charset=utf-8")]), _INDEX_HTML_BYTES)
    if request.path == "/healthz":
        return Response(200, "OK", Headers([("Content-Type", "text/plain")]), b"ok")
    return None


async def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8765))
    async with websockets.serve(handle_client, host, port, process_request=serve_static_or_upgrade):
        print(f"Serving demo (WebSocket + static frontend) on http://{host}:{port}", flush=True)
        await sim_loop()


if __name__ == "__main__":
    asyncio.run(main())
