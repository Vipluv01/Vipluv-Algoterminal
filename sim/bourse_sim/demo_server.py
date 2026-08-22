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

INDEX_HTML_PATH = Path(__file__).resolve().parents[1] / "demo" / "index.html"


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
SIM: LiveSim | None = None


async def broadcast(msg: dict) -> None:
    if not CLIENTS:
        return
    data = json.dumps(msg)
    await asyncio.gather(*(c.send(data) for c in list(CLIENTS)), return_exceptions=True)


async def sim_loop() -> None:
    global SIM
    SIM = LiveSim()
    try:
        while True:
            if not SIM.paused:
                msg = SIM.step()
                await broadcast(msg)
            await asyncio.sleep(SIM.speed_ms / 1000.0)
    finally:
        SIM.close()


async def handle_client(ws) -> None:
    CLIENTS.add(ws)
    try:
        async for raw in ws:
            if SIM is None:
                continue
            try:
                cmd = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if cmd.get("cmd") == "pause":
                SIM.paused = True
            elif cmd.get("cmd") == "resume":
                SIM.paused = False
            elif cmd.get("cmd") == "step":
                m = SIM.step()
                await broadcast(m)
            elif cmd.get("cmd") == "add_informed":
                SIM.add_informed_trader()
            elif cmd.get("cmd") == "set_volatility":
                SIM.set_volatility(float(cmd.get("value", 0.25)))
            elif cmd.get("cmd") == "set_speed":
                SIM.speed_ms = max(10, int(cmd.get("value", 150)))
    finally:
        CLIENTS.discard(ws)


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
