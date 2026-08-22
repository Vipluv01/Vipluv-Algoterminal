"""Market participants whose collective order flow produces the traded price.

Three roles, each contributing a distinct, well-understood ingredient of real
market microstructure:

- NoiseTrader: liquidity-motivated flow uncorrelated with value. Necessary
  because a market of only informed traders converges instantly and trivially
  -- noise trading is what gives the market maker actual adverse-selection
  risk to manage, which is the entire point of a market maker existing.
- InformedTrader: trades toward a private noisy signal of the fundamental.
  This is the source of real price discovery, and (structurally) the source
  of the market maker's losses when its inventory ends up on the wrong side
  of one.
- MarketMaker: quotes both sides continuously, manages inventory risk via
  skew. Evaluated separately in market_maker.py against the Avellaneda-
  Stoikov analytical optimum -- this class is the baseline/reference
  implementation the RL or heuristic variants get compared to.

All price arithmetic here works in the engine's integer TICK space, converted
from the fundamental's continuous value space at the boundary -- keeping that
conversion in one place (`to_ticks`) is what stops a units bug from silently
producing an ever-widening or ever-crossed book.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from engine import Engine, SubmitResult


def to_ticks(value: float, tick_size: float) -> int:
    return int(round(value / tick_size))


def from_ticks(ticks: int, tick_size: float) -> float:
    return ticks * tick_size


@dataclass
class NoiseTrader:
    """Poisson arrival, random side, size drawn from a heavy-tailed
    distribution -- deliberately reusing the same Pareto shape as bourse's
    own Go workload generator (internal/workload), since both are modelling
    the same empirical fact about real order sizes."""

    trader_id: int
    tick_size: float
    rng: np.random.Generator = field(default_factory=np.random.default_rng)
    order_seq: int = field(default=0)
    aggressive_prob: float = 0.2  # fraction that cross immediately (market-like)
                                    # vs rest passively near the touch.
                                    #
                                    # Lowered from an initial 0.5 after a direct trace
                                    # showed the book sitting completely one-sided or
                                    # empty on a majority of steps -- with only one
                                    # market maker (cancel-and-repost every step) and
                                    # noise traders split evenly between resting and
                                    # crossing, there was not enough standing liquidity
                                    # to keep the book populated between refreshes. A
                                    # book that empties out is a real market condition,
                                    # but a book that's ALWAYS momentarily empty is a
                                    # parameterization artifact, not the phenomenon this
                                    # simulation is trying to produce.

    def act(self, eng: Engine, mid_ticks: int, spread_ticks: int) -> SubmitResult | None:
        side = "buy" if self.rng.random() < 0.5 else "sell"
        qty = max(1, int(1.0 / (1 - self.rng.random()) ** (1 / 1.6)))
        qty = min(qty, 200)

        self.order_seq += 1
        oid = self.trader_id * 10_000_000 + self.order_seq

        if self.rng.random() < self.aggressive_prob:
            return eng.submit(order_id=oid, side=side, qty=qty, order_type="market", owner=self.trader_id)

        offset = int(self.rng.integers(0, max(2, spread_ticks)))
        px = mid_ticks - offset if side == "buy" else mid_ticks + offset
        return eng.submit(order_id=oid, side=side, qty=qty, px=px, tif="gtc", owner=self.trader_id)


@dataclass
class InformedTrader:
    """Observes the fundamental through private noise and trades toward it.

    The trading rule is deliberately simple (threshold on perceived
    mispricing) rather than optimal-execution machinery -- the point of this
    agent is to be a clean, well-understood SOURCE of informed order flow for
    the simulation, not itself a research subject. Its own sophistication is
    not what this project is evaluating.
    """

    trader_id: int
    tick_size: float
    signal_noise_sigma: float = 0.01
    threshold_ticks: float = 2.0
    rng: np.random.Generator = field(default_factory=np.random.default_rng)
    order_seq: int = field(default=0)

    def act(self, eng: Engine, fundamental_value: float, mid_ticks: int | None) -> SubmitResult | None:
        if mid_ticks is None:
            return None

        signal = fundamental_value * float(np.exp(self.rng.normal(0, self.signal_noise_sigma)))
        signal_ticks = to_ticks(signal, self.tick_size)
        mispricing = signal_ticks - mid_ticks

        if abs(mispricing) < self.threshold_ticks:
            return None  # no edge worth trading on -- this is what keeps
                          # informed flow from firing every single step

        side = "buy" if mispricing > 0 else "sell"
        qty = min(500, max(10, int(abs(mispricing) * 20)))

        self.order_seq += 1
        oid = self.trader_id * 10_000_000 + self.order_seq
        return eng.submit(order_id=oid, side=side, qty=qty, order_type="market", owner=self.trader_id)


@dataclass
class MarketMaker:
    """Quotes both sides with inventory-skew risk management.

    The skew mechanism is the standard, well-understood heuristic version of
    what Avellaneda-Stoikov formalizes: as inventory grows long, shift both
    quotes down (discourage further buying, encourage selling back to
    neutral); as it grows short, shift up. `market_maker.py` implements the
    actual A-S reservation-price formula separately, so this heuristic
    version and the analytical one can be run side by side and compared --
    this class is the simple reference point, not the final word.
    """

    trader_id: int
    tick_size: float
    base_half_spread_ticks: float = 3.0
    inventory_skew_ticks_per_unit: float = 0.02
    max_inventory: int = 5000
    quote_size: int = 100  # raised from an original 50 after measuring order-flow
                             # sizes directly (Pareto-distributed, up to several
                             # hundred) and confirming small quotes get fully run
                             # through by a single aggressive order far too often.
                             # NOTE: this is a partial calibration, not a resolved
                             # one -- see KNOWN_ISSUES.md. Pushing this much higher
                             # (tested to 400) makes realized P&L sharply worse
                             # without fixing the volatility-clustering sign in the
                             # full simulate.py pipeline, so 100 is a deliberately
                             # conservative middle ground pending a proper fix.
    rng: np.random.Generator = field(default_factory=np.random.default_rng)
    order_seq: int = field(default=0)

    inventory: int = field(default=0, init=False)
    cash: float = field(default=0.0, init=False)
    _live_bid_id: int | None = field(default=None, init=False)
    _live_ask_id: int | None = field(default=None, init=False)
    _live_bid_px: int | None = field(default=None, init=False)
    _live_ask_px: int | None = field(default=None, init=False)

    def _next_id(self) -> int:
        self.order_seq += 1
        return self.trader_id * 10_000_000 + self.order_seq

    def refresh_quotes(self, eng: Engine, mid_ticks: int, vol_estimate: float) -> None:
        """Post new quotes around a reservation price, THEN cancel the old
        ones -- post-before-cancel, not cancel-before-post.

        This ordering matters more than it looks. A direct trace of the
        simulation found the book had a genuine two-sided market on only 3%
        of steps: cancelling first left a window, every single step, where
        this maker (often the only continuous liquidity provider on one or
        both sides) had ZERO resting orders, during which any other
        participant's action saw a one-sided or empty book. That thin-book
        condition then fed into `eng.mid()` returning None 97% of the time,
        which corrupted the recorded price series with long stale runs
        punctuated by jumps -- a structural artifact, not a market dynamic.
        Posting new quotes before cancelling the old keeps continuous
        two-sided presence (briefly 4 resting orders instead of 2, for one
        step) and removes that gap entirely.
        """
        skew = -self.inventory * self.inventory_skew_ticks_per_unit
        half_spread = max(1.0, self.base_half_spread_ticks * (1.0 + vol_estimate))

        reservation = mid_ticks + skew
        bid_px = int(round(reservation - half_spread))
        ask_px = int(round(reservation + half_spread))
        if bid_px >= ask_px:
            ask_px = bid_px + 1  # never quote a crossed or locked market

        old_bid_id, old_ask_id = self._live_bid_id, self._live_ask_id
        old_bid_px, old_ask_px = self._live_bid_px, self._live_ask_px
        self._live_bid_id = self._live_ask_id = None

        # Self-cross guard: posting the new quote before cancelling the old
        # one (see this method's docstring for why post-before-cancel exists
        # at all) is only safe if the new quote does not cross the OLD
        # resting order on the OPPOSITE side -- otherwise the engine (which
        # has no self-trade prevention, a documented gap) would match this
        # maker against itself, and the fill-routing logic upstream would
        # record that as a real inventory change in one direction only,
        # silently corrupting the P&L. Prices are cached from the last call
        # rather than queried from the engine, since the engine's wire
        # protocol has no "look up this order's price" op and doesn't need
        # one just for this. If a cross would occur, cancel the stale
        # opposite-side order FIRST -- accepting a brief one-sided gap only
        # in that specific case -- rather than ever risk a self-trade.
        if old_ask_px is not None and bid_px >= old_ask_px:
            eng.cancel(old_ask_id)
            old_ask_id = None
        if old_bid_px is not None and ask_px <= old_bid_px:
            eng.cancel(old_bid_id)
            old_bid_id = None

        # Both branches explicitly reset the OTHER field's cache to None
        # when that side isn't posted (inventory limit reached) -- id and
        # price must always go stale together. A prior version left a
        # side's cached price behind after its id had already reset to
        # None, so the next call's self-cross guard read a real-looking
        # stale price paired with a None id and called eng.cancel(None),
        # which crashed the Go server outright (a nil pointer dereference
        # on the wire, found via a direct large-quote_size stress test).
        if self.inventory < self.max_inventory:
            self._live_bid_id = self._next_id()
            self._live_bid_px = bid_px
            eng.submit(order_id=self._live_bid_id, side="buy", qty=self.quote_size,
                       px=bid_px, tif="gtc", owner=self.trader_id)
        else:
            self._live_bid_px = None
        if self.inventory > -self.max_inventory:
            self._live_ask_id = self._next_id()
            self._live_ask_px = ask_px
            eng.submit(order_id=self._live_ask_id, side="sell", qty=self.quote_size,
                       px=ask_px, tif="gtc", owner=self.trader_id)
        else:
            self._live_ask_px = None

        if old_bid_id is not None:
            eng.cancel(old_bid_id)
        if old_ask_id is not None:
            eng.cancel(old_ask_id)

    def on_fill(self, side: str, qty: int, px_ticks: int) -> None:
        """Called by the simulation loop for every fill this maker's resting
        orders receive, to keep inventory/cash correct for the P&L and
        adverse-selection decomposition done downstream."""
        if side == "buy":
            self.inventory += qty
            self.cash -= qty * px_ticks
        else:
            self.inventory -= qty
            self.cash += qty * px_ticks

    def mark_to_market(self, mid_ticks: int) -> float:
        """P&L in real currency units, not ticks.

        `cash` and `inventory` are accumulated purely from tick-priced fills
        (see on_fill), so the whole expression is in tick-units until this
        conversion -- multiplying by tick_size here, once, at the boundary
        where the number is actually reported, is what keeps a 10,000-tick
        price from being read as a 10,000-currency-unit one.
        """
        return (self.cash + self.inventory * mid_ticks) * self.tick_size
