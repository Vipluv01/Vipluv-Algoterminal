"""The Avellaneda-Stoikov (2008) optimal market-making model -- the
analytical baseline the heuristic MarketMaker in agents.py is measured
against.

Why this comparison matters for the project: agents.py's MarketMaker uses a
plausible, standard HEURISTIC for inventory skew (shift quotes linearly with
position). It is easy to build something that "looks like" a market maker.
It is a different, harder claim to show that heuristic is actually doing
something close to the RIGHT thing -- and the way to substantiate that claim
is to compare it against a strategy derived by solving the maker's actual
optimization problem (maximize expected terminal utility of wealth, under
inventory risk, via HJB / stochastic control), rather than another guess.

The A-S model gives two closed-form results:

  reservation price:  r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
  optimal spread:      delta = gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/k)

`s` is mid-price, `q` is current inventory, `gamma` is the maker's risk
aversion, `sigma` is the (estimated) volatility of the underlying, `T - t` is
time remaining in the trading session, and `k` describes how fast order
arrival intensity decays with quoted distance from mid (a liquidity/order-
book-depth parameter, estimated from the simulated book itself).

The reservation price is the key idea: it is NOT the mid-price. It is the
mid, shifted away from a non-zero inventory position -- shifted DOWN when
long (encouraging the model to sell back to flat) and UP when short. Quoting
symmetrically around this shifted price, rather than around the raw mid, is
what makes the strategy risk-averse to inventory in a way that is
provably optimal under the model's assumptions, not just heuristically
plausible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class AvellanedaStoikovParams:
    gamma: float = 0.1     # risk aversion: higher = quotes further from mid, tighter inventory control
    k: float = 1.5         # order-arrival decay rate; estimated from the book, see estimate_k below
    session_length: float = 1.0  # T, in the same time units as `t` passed to quote()


def estimate_k(depth_at_ticks: dict[int, float], *, previous_k: float | None = None,
                min_points: int = 4) -> float:
    """Estimate the order-arrival intensity decay parameter k from observed
    book depth at increasing distance from the touch.

    The model assumes arrival intensity lambda(delta) = A * exp(-k * delta):
    orders arrive less often the further a quote sits from the fair price.
    Resting depth at a given distance is a reasonable proxy for how quickly
    that distance gets "found" by aggressive flow, so k is estimated by a
    log-linear fit of depth against distance -- log(depth) ~ log(A) - k*delta.

    On a thin, sparsely-populated simulated book, a fit with only 2-3 points
    is dominated by noise and swings wildly between calls -- which then
    feeds directly into the spread formula and destabilizes the maker's own
    quoting (this was diagnosed directly: k alternating between a floored
    0.1 and an arbitrary 1.5 default caused reservation-price drift that
    lost money independent of whether the underlying strategy was sound).
    The fix is two-fold: require `min_points` distinct depth levels before
    trusting the fit at all, and fall back to `previous_k` (last known good
    estimate) rather than an arbitrary floor when the data doesn't support a
    fit -- holding the last good estimate is a far smaller perturbation to
    the maker's quotes than snapping to a hardcoded constant.
    """
    fallback = previous_k if previous_k is not None else 1.5
    if len(depth_at_ticks) < min_points:
        return fallback

    ticks = np.array(sorted(depth_at_ticks.keys()))
    depths = np.array([max(depth_at_ticks[t], 1e-6) for t in ticks])
    slope, _ = np.polyfit(ticks, np.log(depths), 1)
    if slope >= 0:
        # Non-decaying (flat or increasing) depth doesn't match the model's
        # assumption at all -- the fit isn't meaningful, so hold steady
        # rather than manufacture a number from a fit that contradicts its
        # own premise.
        return fallback
    return float(max(-slope, 0.05))


@dataclass
class AvellanedaStoikovMaker:
    """Same external interface as agents.MarketMaker (refresh_quotes,
    on_fill, mark_to_market, inventory/cash bookkeeping) so it can be
    dropped into the same simulation loop and compared apples-to-apples --
    the only thing that differs between the two is HOW the quote prices are
    chosen.
    """

    trader_id: int
    tick_size: float
    params: AvellanedaStoikovParams = field(default_factory=AvellanedaStoikovParams)
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
    max_inventory: int = 5000

    inventory: int = field(default=0, init=False)
    cash: float = field(default=0.0, init=False)
    order_seq: int = field(default=0, init=False)
    _live_bid_id: int | None = field(default=None, init=False)
    _live_ask_id: int | None = field(default=None, init=False)
    _live_bid_px: int | None = field(default=None, init=False)
    _live_ask_px: int | None = field(default=None, init=False)

    def _next_id(self) -> int:
        self.order_seq += 1
        return self.trader_id * 10_000_000 + self.order_seq

    def reservation_price_ticks(self, mid_ticks: int, sigma_ticks: float, time_remaining: float) -> float:
        """r = s - q * gamma * sigma^2 * (T - t), all in tick-price units so
        it composes directly with the engine's integer price space."""
        p = self.params
        return mid_ticks - self.inventory * p.gamma * (sigma_ticks ** 2) * time_remaining

    def optimal_half_spread_ticks(self, sigma_ticks: float, time_remaining: float) -> float:
        p = self.params
        inventory_term = p.gamma * (sigma_ticks ** 2) * time_remaining
        liquidity_term = (2.0 / p.gamma) * math.log(1.0 + p.gamma / p.k)
        return 0.5 * (inventory_term + liquidity_term)

    def refresh_quotes(self, eng, mid_ticks: int, sigma_ticks: float, time_remaining: float,
                        depth_at_ticks: dict[int, float] | None = None) -> None:
        """Post-before-cancel, matching agents.MarketMaker.refresh_quotes --
        see that method's docstring for why: cancelling first leaves a
        one-step window with zero resting orders on this maker's side,
        which was directly measured to leave the book two-sided on only 3%
        of steps and corrupted the recorded mid-price series with long
        stale runs. Posting first removes that window."""
        if depth_at_ticks:
            self.params.k = estimate_k(depth_at_ticks, previous_k=self.params.k)

        r = self.reservation_price_ticks(mid_ticks, sigma_ticks, max(time_remaining, 1e-6))
        half_spread = max(1.0, self.optimal_half_spread_ticks(sigma_ticks, max(time_remaining, 1e-6)))

        bid_px = int(round(r - half_spread))
        ask_px = int(round(r + half_spread))
        if bid_px >= ask_px:
            ask_px = bid_px + 1

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
        if side == "buy":
            self.inventory += qty
            self.cash -= qty * px_ticks
        else:
            self.inventory -= qty
            self.cash += qty * px_ticks

    def mark_to_market(self, mid_ticks: int) -> float:
        return (self.cash + self.inventory * mid_ticks) * self.tick_size
