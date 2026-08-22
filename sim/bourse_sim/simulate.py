"""Ties the fundamental process, the three agent types, and the Go engine
together into one simulation session, and records what actually traded.

The critical correctness point in this file is fill attribution: a Fill from
the engine names a maker_id and a taker_id, which are ORDER ids, not trader
ids. Whoever owns that order determines whether the market maker's inventory
moved and in which direction -- getting this wrong would silently corrupt
every P&L number downstream, which is exactly the kind of bug that's easy to
introduce and easy to miss, so `_owner_of` and the fill-routing logic below
are covered directly by tests rather than trusted by inspection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from agents import InformedTrader, MarketMaker, NoiseTrader
from engine import Engine, Fill
from fundamental import FundamentalProcess


def owner_of_order_id(order_id: int) -> int:
    """Inverse of the `trader_id * 10_000_000 + seq` scheme every agent uses
    to mint order ids. Centralised here so the encoding only has one
    consumer to keep in sync if it ever changes."""
    return order_id // 10_000_000


@dataclass
class SimResult:
    trade_prices: np.ndarray      # traded price (float, converted from ticks) per fill
    trade_times: np.ndarray       # step index per fill
    mid_price_path: np.ndarray    # best-bid/best-ask midpoint at each step -- the
                                   # series stylized-facts analysis should use, NOT
                                   # trade_prices. Trade-to-trade prices alternate
                                   # between hitting the bid and the ask (bid-ask
                                   # bounce, Roll 1984), which mechanically injects a
                                   # large SPURIOUS negative return autocorrelation
                                   # that has nothing to do with the market's actual
                                   # dynamics. Mid-price is free of that artifact.
    fundamental_path: np.ndarray  # true value at each step (for validation only --
                                   # NOT observable by any agent during the run)
    # Aggregate (summed) across every maker in the simulation. For the
    # single-maker case these are identical to that one maker's own path --
    # multi-maker support is additive, not a breaking change to this field's
    # meaning. Net inventory across makers is itself a real systemic-risk
    # signal (are makers collectively leaning one direction, or offsetting).
    mm_inventory_path: np.ndarray
    mm_pnl_path: np.ndarray
    # Per-maker breakdown, same order as the makers list passed in (or a
    # length-1 list for the single-maker case).
    per_maker_final_inventory: list[int] = field(default_factory=list)
    per_maker_final_pnl: list[float] = field(default_factory=list)


def run_simulation(
    *,
    steps: int,
    tick_size: float = 0.01,
    s0: float = 100.0,
    fundamental_sigma: float = 0.25,
    n_noise_traders: int = 20,
    n_informed_traders: int = 5,
    seed: int = 0,
    maker: MarketMaker | list[MarketMaker] | None = None,
) -> SimResult:
    rng = np.random.default_rng(seed)
    min_px = to_ticks_static(s0 * 0.5, tick_size)
    max_px = to_ticks_static(s0 * 2.0, tick_size)

    fundamental = FundamentalProcess(s0=s0, sigma=fundamental_sigma, seed=seed)

    noise_traders = [
        NoiseTrader(trader_id=100 + i, tick_size=tick_size,
                    rng=np.random.default_rng(rng.integers(0, 2**31)))
        for i in range(n_noise_traders)
    ]
    informed_traders = [
        InformedTrader(trader_id=200 + i, tick_size=tick_size,
                        rng=np.random.default_rng(rng.integers(0, 2**31)))
        for i in range(n_informed_traders)
    ]
    # Injectable rather than always constructed here: a parameter sweep
    # (e.g. testing inventory_skew_ticks_per_unit, or how many independent
    # makers the touch has) needs to run THROUGH this exact pipeline, not a
    # hand-copied reimplementation of it -- an earlier ad-hoc test script
    # that reimplemented this loop independently produced results that
    # silently diverged from what this function actually does, twice.
    #
    # Accepts a single maker (the common case) or a list (for testing
    # whether multiple independent liquidity providers changes the
    # simulation's dynamics -- e.g. the mid-level bid-ask-bounce-like
    # artifact documented in KNOWN_ISSUES.md, hypothesized to come from one
    # dominant quoter re-centering the touch every step).
    if maker is None:
        makers = [MarketMaker(trader_id=1, tick_size=tick_size,
                               rng=np.random.default_rng(rng.integers(0, 2**31)))]
    elif isinstance(maker, list):
        makers = maker
    else:
        makers = [maker]
    if len({m.trader_id for m in makers}) != len(makers):
        raise ValueError("makers must have distinct trader_id values")
    maker_ids = {m.trader_id for m in makers}
    maker_by_id = {m.trader_id: m for m in makers}

    trade_prices: list[float] = []
    trade_times: list[int] = []
    fundamental_path = np.zeros(steps)
    mid_price_path = np.zeros(steps)
    mm_inventory_path = np.zeros(steps, dtype=np.int64)
    mm_pnl_path = np.zeros(steps)

    recent_returns: list[float] = []
    last_mid: int | None = None

    with Engine(min_px=min_px, max_px=max_px, tick=1, capacity=1 << 18) as eng:
        # Tracks the last OBSERVED mid, updated only when eng.mid() returns a
        # real value. This -- not the fixed seed price -- is the correct
        # fallback whenever the book is momentarily one-sided or empty
        # (which happens often: a market maker with exactly one resting bid
        # and one resting ask gets both consumed between refreshes far more
        # often than a deep multi-level book would). Falling back to the
        # ORIGINAL seed price instead, as an earlier version of this
        # function did, silently snapped the recorded price back to exactly
        # the starting value every time the book emptied -- a real,
        # diagnosed bug that fabricated large synthetic price jumps and
        # inflated the stylized-facts kurtosis measurement with an artifact
        # rather than genuine market dynamics.
        # Seed the book so mid-price queries don't return None on step 0.
        start_ticks = to_ticks_static(s0, tick_size)
        # Small, deliberately: sized to be comparable to ordinary order flow so
        # it gets consumed within the first few real trades, rather than
        # persisting as an oversized anchor that keeps eng.mid() pinned to
        # the seed price whenever a maker's own spread temporarily widens
        # past it (diagnosed directly: a 1000-qty seed caused exactly this
        # with the Avellaneda-Stoikov maker during an unstable k estimate).
        eng.submit(order_id=999_000_001, side="buy", qty=30, px=start_ticks - 5, owner=999)
        eng.submit(order_id=999_000_002, side="sell", qty=30, px=start_ticks + 5, owner=999)
        last_known_mid_ticks = start_ticks

        for t in range(steps):
            fv = fundamental.step()
            fundamental_path[t] = fv

            mid = eng.mid()
            if mid is not None:
                last_known_mid_ticks = int(round(mid))
            mid_ticks = last_known_mid_ticks
            bid = eng.best_bid()
            ask = eng.best_ask()
            spread_ticks = (ask[0] - bid[0]) if (bid and ask) else 4

            vol_estimate = float(np.std(recent_returns[-50:])) if len(recent_returns) >= 10 else 0.0
            for m in makers:
                m.refresh_quotes(eng, mid_ticks, vol_estimate)

            for nt in noise_traders:
                if rng.random() < 0.3:
                    _route_fills(nt.act(eng, mid_ticks, spread_ticks), maker_by_id,
                                 tick_size, trade_prices, trade_times, t)

            for it in informed_traders:
                if rng.random() < 0.5:
                    _route_fills(it.act(eng, fv, mid_ticks if bid and ask else None), maker_by_id,
                                 tick_size, trade_prices, trade_times, t)

            new_mid = eng.mid()
            if new_mid is not None:
                if last_mid is not None and last_mid > 0:
                    recent_returns.append(np.log(new_mid / last_mid))
                last_mid = new_mid
                last_known_mid_ticks = int(round(new_mid))
            mark_mid_ticks = last_known_mid_ticks

            mid_price_path[t] = mark_mid_ticks * tick_size
            mm_inventory_path[t] = sum(m.inventory for m in makers)
            mm_pnl_path[t] = sum(m.mark_to_market(mark_mid_ticks) for m in makers)

    final_mid_ticks = last_known_mid_ticks
    return SimResult(
        trade_prices=np.array(trade_prices) * tick_size,
        trade_times=np.array(trade_times),
        mid_price_path=mid_price_path,
        fundamental_path=fundamental_path,
        mm_inventory_path=mm_inventory_path,
        mm_pnl_path=mm_pnl_path,
        per_maker_final_inventory=[m.inventory for m in makers],
        per_maker_final_pnl=[m.mark_to_market(final_mid_ticks) for m in makers],
    )


def to_ticks_static(value: float, tick_size: float) -> int:
    return int(round(value / tick_size))


def _route_fills(
    result,
    maker_by_id: dict[int, MarketMaker],
    tick_size: float,
    trade_prices: list[float],
    trade_times: list[int],
    t: int,
) -> None:
    if result is None:
        return
    for f in result.fills:
        trade_prices.append(f.px)
        trade_times.append(t)

        maker_order_owner = owner_of_order_id(f.maker_id)
        taker_order_owner = owner_of_order_id(f.taker_id)

        # Independent checks, NOT if/elif: with multiple makers, a maker's
        # resting order can be hit by a DIFFERENT maker's aggressive order
        # (inter-maker trading), and both sides' inventories need updating.
        # The single-maker version of this used if/elif, which silently
        # dropped one side's inventory update in exactly that scenario --
        # harmless with one maker (the two branches were mutually exclusive
        # by construction), a real bug the moment a second maker exists.
        resting_maker = maker_by_id.get(maker_order_owner)
        if resting_maker is not None:
            resting_side = "sell" if f.taker_side == "buy" else "buy"
            resting_maker.on_fill(resting_side, f.qty, f.px)

        aggressing_maker = maker_by_id.get(taker_order_owner)
        if aggressing_maker is not None:
            aggressing_maker.on_fill(f.taker_side, f.qty, f.px)
