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
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.telemetry import record_order_submit_latency_ms

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

# NIFTY50 and BANKNIFTY as DERIVED weighted baskets of the constituents
# above -- not their own SymbolMarket/Engine instance (see this module's
# own docstring: no new subprocess, and honest about being a synthetic
# index PROXY rather than a separately-matched instrument). Real NSE index
# weights are free-float-market-cap-weighted with a divisor normalizing the
# level to a base value (~1000 at launch); this project has no market-cap
# data for its 7 constituents to weight by, so these are EQUAL-weighted
# (weights sum to 1, I_t = sum(w_i * S_i(t))) -- an honestly-simplified
# proxy, not an attempt at a real index level. BANKNIFTY's constituents are
# the 3 bank names already in NAMED_INSTRUMENTS (the same pair
# pairs_cointegration.py trades, plus SBIN); NIFTY50 uses all 7 as a
# broad-market proxy, despite the real index having 50 names -- there are
# only 7 simulated instruments to draw from at all.
DERIVED_INDICES: dict[str, dict[str, float]] = {
    "NIFTY50": {sym: 1.0 / len(NAMED_INSTRUMENTS) for sym in NAMED_INSTRUMENTS},
    "BANKNIFTY": {sym: 1.0 / 3 for sym in ("ICICIBANK", "HDFCBANK", "SBIN")},
}

# The engine `owner` id the human paper-trading user's own orders submit
# under -- app/routers/orders.py, app/brackets.py (bracket exits), and
# app/pairs_service.py (sub-account clones) all use this. Found while
# building the leaderboard (real bot P&L needs each participant's fills
# distinguishable by owner id): those three call sites previously used the
# literal 1, which SymbolMarket's own MarketMaker ALSO uses as its
# trader_id -- the human user's manual/strategy orders and the market
# maker bot's quoting activity were silently indistinguishable in the
# engine's own fill/position records. 0 is otherwise unused across every
# reserved range this file's other owner ids occupy (noise=100-119,
# informed=200-204, maker=1, seed-liquidity=999).
HUMAN_USER_OWNER_ID = 0


def compute_derived_index(symbol: str, prices: dict[str, float]) -> float:
    """I_t = sum(w_i * S_i(t)) over symbol's own constituent weights."""
    weights = DERIVED_INDICES[symbol]
    return sum(w * prices[constituent] for constituent, w in weights.items())


UNKNOWN_OWNER = -1  # sentinel for an order whose owner couldn't be looked
# up -- see SymbolMarket._order_owner's own docstring for the one way this
# can legitimately happen (a still-resting order's id was pruned from that
# bounded map). Never a real owner id (every real range starts at 0).


@dataclass(frozen=True)
class RecordedFill:
    """One fill, tagged with what the engine's own Fill (sim/bourse_sim/
    engine.py) cannot carry: which symbol it happened on and this market's
    own step counter. The engine has no concept of a symbol at all, and its
    Fill.px is an integer tick -- this converts to real currency once here
    so every consumer doesn't have to know this market's tick_size.

    Feeds the leaderboard (real bot P&L, not simulated for display) and the
    VWAP volume profile -- both need what actually traded, not just the
    price series.

    taker_id/maker_id are ENGINE ORDER ids (internal/book/types.go's
    Fill.TakerID/MakerID), NOT owner ids -- do not use them to attribute a
    fill to a trader. taker_owner/maker_owner are the actual owner ids
    (looked up via SymbolMarket._order_owner at the moment this fill was
    recorded), which is what the leaderboard actually needs.
    """

    seq: int
    taker_id: int
    maker_id: int
    px: float
    qty: int
    taker_side: str
    symbol: str
    step: int
    taker_owner: int
    maker_owner: int


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

        # price_collar_bps and position_limit were threaded through
        # wireConfig on the Go side but never actually passed from here, so
        # both risk checks defaulted to off (0 disables). Both values are
        # measured, not guessed: probing all 7 symbols for 800 steps found a
        # worst-case limit-price deviation of 7.4bps from the last trade and
        # a worst-case market-maker inventory of 3,957.
        #
        # These checks apply to EVERY order this book sees, including the
        # simulated agents' own -- not just the web app's user-submitted
        # orders. That is why both thresholds sit well above the measured
        # noise floor rather than close to it: a limit tight enough to bind
        # on ordinary maker/informed-trader activity would silently distort
        # the simulation's price dynamics, the same class of bug the
        # stale-mid fallback bug in sim/KNOWN_ISSUES.md already was. The
        # goal here is "invisible in normal operation, catches a genuine
        # fat-finger or runaway order," not "tight."
        #   price_collar_bps=500 (5%)  is ~67x the observed 7.4bps ceiling.
        #   position_limit=20_000      is ~5x the observed 3,957 peak.
        self.eng = Engine(
            min_px=self.min_px, max_px=self.max_px, tick=1, capacity=1 << 18,
            price_collar_bps=500, position_limit=20_000,
        )

        # Bounded so a long-running process (or a long backtest) never grows
        # this unbounded -- 500 is a few seconds of ordinary tick activity at
        # this market's step rate, which is all any current consumer
        # (leaderboard, VWAP) needs to look back over.
        self.recent_fills: deque[RecordedFill] = deque(maxlen=500)
        self.recent_volume: deque[int] = deque(maxlen=500)  # traded qty per step()
        self._step_count = 0
        self._current_step_volume = 0
        # order_id -> owner, populated at submit time (see
        # _wrap_engine_submit_to_record_fills) -- the engine's own Fill
        # only carries order ids (see RecordedFill's docstring), so this is
        # the only way to answer "which OWNER made this fill." Bounded for
        # the same reason recent_fills is: a resting order can wait
        # indefinitely, but this only needs to cover orders a CURRENTLY
        # retained fill could still reference -- 500 fills touch at most
        # 1000 distinct order ids, so 4000 is comfortable headroom, not a
        # tight fit.
        self._order_owner: dict[int, int] = {}
        self._wrap_engine_submit_to_record_fills()

        start_ticks = to_ticks_static(self.s0, self.tick_size)
        self.eng.submit(order_id=999_000_001, side="buy", qty=30, px=start_ticks - 5, owner=999)
        self.eng.submit(order_id=999_000_002, side="sell", qty=30, px=start_ticks + 5, owner=999)
        self.last_known_mid_ticks = start_ticks
        self.last_mid: float | None = None
        self.recent_returns: list[float] = []
        self.price_history.append(self.s0)
        # Reserved order-id range for orders the web app itself submits
        # (paper trades, and eventually live-confirmed orders) -- clear of
        # every bot range this market's own agents use (noise=100-119,
        # informed=200+, maker=1), the seed-liquidity owner (999), and the
        # human user's own OWNER id (0, HUMAN_USER_OWNER_ID above -- a
        # separate namespace from this one, order ids and owner ids don't
        # share a range).
        self._next_order_seq = 500_000

    def _wrap_engine_submit_to_record_fills(self) -> None:
        """Every fill this book produces goes through Engine.submit,
        regardless of which caller triggered it: a bot's act(), the market
        maker's refresh_quotes, or the web app's own order submission
        (routers/orders.py calls market.eng.submit directly, the same bound
        method this wraps). Instrumenting the method once here, rather than
        threading a callback through every one of those callers, is what
        lets recent_fills capture all of them without touching agents.py or
        the routers.

        This is also the ONE place that sees the raw Python<->simserver IPC
        round-trip for a real submit call (see app.telemetry's own
        docstring on why that's a genuinely different measurement from the
        Go benchmark's in-process numbers) -- timed here rather than in
        routers/orders.py specifically so bot-driven submits contribute
        samples too, the same real subprocess pipe under the same load a
        human order actually experiences.
        """
        raw_submit = self.eng.submit
        MAX_TRACKED_ORDER_OWNERS = 4000

        def tracking_submit(*args, **kwargs):
            # Recorded BEFORE the call, keyed by the id THIS order is being
            # submitted under -- every real caller in this codebase passes
            # both order_id and owner as keywords (routers/orders.py,
            # brackets.py, pairs_service.py, agents.py's own callers), so
            # this is the taker's owner for whatever fills come back below.
            order_id = kwargs.get("order_id")
            owner = kwargs.get("owner", 0)
            if order_id is not None:
                self._order_owner[order_id] = owner
                if len(self._order_owner) > MAX_TRACKED_ORDER_OWNERS:
                    self._order_owner.pop(next(iter(self._order_owner)))

            start = time.perf_counter()
            result = raw_submit(*args, **kwargs)
            record_order_submit_latency_ms((time.perf_counter() - start) * 1000.0)
            for f in result.fills:
                self.recent_fills.append(RecordedFill(
                    seq=f.seq, taker_id=f.taker_id, maker_id=f.maker_id,
                    px=f.px * self.tick_size, qty=f.qty, taker_side=f.taker_side,
                    symbol=self.symbol, step=self._step_count,
                    taker_owner=self._order_owner.get(f.taker_id, UNKNOWN_OWNER),
                    maker_owner=self._order_owner.get(f.maker_id, UNKNOWN_OWNER),
                ))
                self._current_step_volume += f.qty
            return result

        self.eng.submit = tracking_submit

    def close(self) -> None:
        self.eng.close()

    def next_order_id(self) -> int:
        self._next_order_seq += 1
        return self._next_order_seq

    @property
    def current_price(self) -> float:
        return self.price_history[-1]

    @property
    def step_count(self) -> int:
        """Public read of the private step counter -- app/execution/
        slicer.py needs a "how many bars has this parent order lived
        through" figure, and this is the same counter fills are already
        tagged with (RecordedFill.step above), just exposed rather than
        re-derived a second way."""
        return self._step_count

    def step(self) -> float:
        """Advances this symbol's own simulation by one step -- identical
        mechanics to demo_server.py's LiveSim.step(), minus the WebSocket
        broadcast, since this runs headless behind the strategy/execution
        layer rather than a live browser demo."""
        self._step_count += 1
        self._current_step_volume = 0
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
        # Recorded from _current_step_volume (accumulated by
        # _wrap_engine_submit_to_record_fills as this step's agents traded)
        # rather than a fresh eng.stats() call -- stats() is a real IPC round
        # trip, and step() already costs ~17 of those; this way volume is
        # free, derived from fills already being recorded for the leaderboard.
        self.recent_volume.append(self._current_step_volume)
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

    def price_history_for(self, symbol: str) -> np.ndarray:
        """Full price history for `symbol` -- a real instrument's own
        price_history, or (for a derived index) the WEIGHTED SUM of its
        constituents' price_history arrays, elementwise. Every constituent
        stays the same length as every other (step_all() advances them
        all together, every tick), so the elementwise combination is
        always well-defined. This is what app/options/chain.py's realized-
        vol calibration reads -- an index has no SymbolMarket of its own
        to hold a price_history, but its derived VALUE has a perfectly
        well-defined history once its constituents' do.
        """
        weights = DERIVED_INDICES.get(symbol)
        if weights is None:
            return self.prices(symbol)
        histories = [self.prices(sym) for sym in weights]
        return sum(w * h for (sym, w), h in zip(weights.items(), histories))

    def current_prices(self) -> dict[str, float]:
        """Every real constituent's live price, PLUS every derived index's
        current value computed from those same prices (app/options/chain.py
        and accounting.compute_account both need "the current price of
        symbol X" to work uniformly whether X is a real, simulated
        instrument or a derived basket -- merging the index values in here,
        once, is what lets every downstream caller treat them the same way
        rather than special-casing derived symbols at every call site)."""
        prices = {sym: m.current_price for sym, m in self.markets.items()}
        for index_symbol, weights in DERIVED_INDICES.items():
            # Skip an index whose constituents aren't all present in THIS
            # registry -- the real app always constructs MarketRegistry
            # with the full 7-symbol universe, so this only ever matters
            # for tests that intentionally build a smaller, partial
            # registry (e.g. just "ICICIBANK") to keep a fixture cheap;
            # such a registry genuinely has nothing to compute BANKNIFTY
            # or NIFTY50 from, and should return a plain equity-only
            # prices dict rather than crash.
            if all(sym in prices for sym in weights):
                prices[index_symbol] = compute_derived_index(index_symbol, prices)
        return prices

    @property
    def current_step(self) -> int:
        """The shared bar index every symbol is on -- step_all() advances
        every SymbolMarket together in one call, so all of them stay in
        lockstep; this reads any one of them (arbitrarily, the first) as
        the registry-wide counter app/execution/slicer.py's start_bar/
        current_bar bookkeeping needs. Raises if the registry has no
        symbols at all, which would mean nothing here could mean anything
        anyway."""
        return next(iter(self.markets.values())).step_count

    def __getitem__(self, symbol: str) -> SymbolMarket:
        if symbol not in self.markets:
            raise KeyError(f"unknown symbol {symbol!r} -- available: {sorted(self.markets)}")
        return self.markets[symbol]

    def close(self) -> None:
        for m in self.markets.values():
            m.close()
