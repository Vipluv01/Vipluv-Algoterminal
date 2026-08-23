"""Named, per-symbol paper markets.

The gap this fixes: bourse's engine is deliberately single-instrument (see
bourse/README.md's "Explicitly not built" -- multi-instrument sharding is
out of scope for the matching engine itself, by design). That's the right
call for the ENGINE, which stays untouched. But a real trading terminal
needs multiple, clearly-identified tradable symbols, not one anonymous
instrument nobody can name -- so this module runs one bourse Engine
INSTANCE per symbol, orchestrated here in Python, rather than making the Go
engine itself multi-instrument.

Symbol universe is NSE large-caps, not US tickers or crypto like Bull's
demo used. That choice isn't cosmetic: live mode is meant to connect to
Zerodha/Groww, both NSE brokers, so paper mode has to trade the SAME
symbols live mode will -- otherwise a strategy "proven" in paper mode on
the wrong universe proves nothing about how it'll behave live. Includes
ICICIBANK and HDFCBANK specifically because they're the pair
pairs_cointegration.py was validated against in icici_mean_reversion.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# bourse_sim's own modules are flat (engine.py, agents.py, ...), not a
# `bourse_sim.` package -- matching the convention its own tests and
# demo_server.py already use, rather than inventing a different import
# style just for this file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sim" / "bourse_sim"))

from agents import InformedTrader, MarketMaker, NoiseTrader  # noqa: E402
from engine import Engine  # noqa: E402
from fundamental import FundamentalProcess  # noqa: E402
from simulate import to_ticks_static  # noqa: E402

# Starting prices are illustrative reference points (approximate real-world
# levels as of when this was written), not a live quote -- paper mode's
# price still EMERGES from simulated order flow from here, exactly like
# bourse's existing demo; only the starting point and the symbol's identity
# are new.
NAMED_INSTRUMENTS: dict[str, float] = {
    "ICICIBANK": 1250.0,
    "HDFCBANK": 1650.0,
    "RELIANCE": 2900.0,
    "TCS": 4150.0,
    "INFY": 1850.0,
    "SBIN": 810.0,
    "TATAMOTORS": 980.0,
}


@dataclass
class SymbolMarket:
    """One named instrument's own order book, simulated agent population,
    and price history -- independent of every other symbol's market, the
    same way two different stocks' order books are independent on a real
    exchange."""

    symbol: str
    tick_size: float = 0.05  # NSE's actual minimum tick for most equities
    s0: float = 100.0
    fundamental_sigma: float = 0.2
    seed: int = 0

    price_history: list[float] = field(default_factory=list, init=False)

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

        self.eng = Engine(min_px=self.min_px, max_px=self.max_px, tick=1, capacity=1 << 18)
        start_ticks = to_ticks_static(self.s0, self.tick_size)
        self.eng.submit(order_id=999_000_001, side="buy", qty=30, px=start_ticks - 5, owner=999)
        self.eng.submit(order_id=999_000_002, side="sell", qty=30, px=start_ticks + 5, owner=999)
        self.last_known_mid_ticks = start_ticks
        self.last_mid: float | None = None
        self.recent_returns: list[float] = []
        self.price_history.append(self.s0)

    def close(self) -> None:
        self.eng.close()

    def step(self) -> float:
        """Advances this symbol's own simulation by one step -- identical
        mechanics to demo_server.py's LiveSim.step(), minus the WebSocket
        broadcast, since this runs headless behind the strategy/execution
        layer rather than a live browser demo."""
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

        for nt in self.noise_traders:
            if self.rng.random() < 0.3:
                nt.act(self.eng, mid_ticks, spread_ticks)
        for it in self.informed_traders:
            if self.rng.random() < 0.5:
                it.act(self.eng, self.fundamental.value, mid_ticks if bid and ask else None)

        new_mid = self.eng.mid()
        if new_mid is not None:
            if self.last_mid is not None and self.last_mid > 0:
                self.recent_returns.append(np.log(new_mid / self.last_mid))
            self.last_mid = new_mid
            self.last_known_mid_ticks = int(round(new_mid))

        mark = self.last_known_mid_ticks * self.tick_size
        self.price_history.append(mark)
        return mark


class MarketRegistry:
    """Owns one SymbolMarket per named instrument. This is the object the
    strategy/execution layer talks to -- it never touches an Engine
    directly, only ever a named symbol's price history, the same way a
    real strategy would query a market data feed by ticker, not by
    knowing which exchange server happens to host it."""

    def __init__(self, symbols: dict[str, float] | None = None, seed: int = 0):
        self._symbols = symbols or NAMED_INSTRUMENTS
        self.markets: dict[str, SymbolMarket] = {
            sym: SymbolMarket(symbol=sym, s0=start_price, seed=seed + i)
            for i, (sym, start_price) in enumerate(self._symbols.items())
        }

    def step_all(self) -> dict[str, float]:
        return {sym: m.step() for sym, m in self.markets.items()}

    def prices(self, symbol: str) -> np.ndarray:
        if symbol not in self.markets:
            raise KeyError(f"unknown symbol {symbol!r} -- available: {sorted(self.markets)}")
        return np.array(self.markets[symbol].price_history)

    def close(self) -> None:
        for m in self.markets.values():
            m.close()
