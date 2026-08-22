"""Runs the heuristic MarketMaker and the AvellanedaStoikovMaker through
IDENTICAL market conditions -- same fundamental path, same noise/informed
order flow, same seed -- so any difference in outcome is attributable to the
quoting strategy itself, not to one of them getting an easier session.

This is the actual comparison the project's flagship claim rests on: not
"the heuristic maker made money" (almost any reasonable maker does, most of
the time, in a simulation with positive expected spread capture), but
"here is how it performs specifically relative to the theoretically
justified analytical strategy."
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from agents import InformedTrader, MarketMaker, NoiseTrader
from avellaneda_stoikov import AvellanedaStoikovMaker, AvellanedaStoikovParams
from engine import Engine
from fundamental import FundamentalProcess
from simulate import owner_of_order_id, to_ticks_static


@dataclass
class MakerRunResult:
    label: str
    pnl_path: np.ndarray
    inventory_path: np.ndarray
    n_fills: int
    final_pnl: float
    pnl_std: float          # volatility of P&L changes -- risk, not just return
    sharpe_like: float      # mean(dPnL) / std(dPnL) -- unannualized, comparative only
    max_abs_inventory: int
    mean_abs_inventory: float


def _run_one_maker(
    maker,
    *,
    steps: int,
    tick_size: float,
    s0: float,
    fundamental_sigma: float,
    n_noise_traders: int,
    n_informed_traders: int,
    seed: int,
    is_avellaneda_stoikov: bool,
) -> MakerRunResult:
    """Replays IDENTICAL agent decisions across both maker types by seeding
    every random source identically -- the noise and informed traders make
    the exact same sequence of decisions in both runs, since their own RNGs
    are seeded purely from `seed`, independent of which maker is present.
    Only the maker's OWN quotes differ between runs.
    """
    rng = np.random.default_rng(seed)
    min_px = to_ticks_static(s0 * 0.5, tick_size)
    max_px = to_ticks_static(s0 * 2.0, tick_size)

    fundamental = FundamentalProcess(s0=s0, sigma=fundamental_sigma, seed=seed)
    noise_traders = [
        NoiseTrader(trader_id=100 + i, tick_size=tick_size, rng=np.random.default_rng(rng.integers(0, 2**31)))
        for i in range(n_noise_traders)
    ]
    informed_traders = [
        InformedTrader(trader_id=200 + i, tick_size=tick_size, rng=np.random.default_rng(rng.integers(0, 2**31)))
        for i in range(n_informed_traders)
    ]
    maker_ids = {maker.trader_id}

    pnl_path = np.zeros(steps)
    inventory_path = np.zeros(steps, dtype=np.int64)
    n_fills = 0
    recent_returns: list[float] = []
    last_mid: float | None = None

    with Engine(min_px=min_px, max_px=max_px, tick=1, capacity=1 << 18) as eng:
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
            fundamental.step()
            mid = eng.mid()
            if mid is not None:
                last_known_mid_ticks = int(round(mid))
            mid_ticks = last_known_mid_ticks
            bid = eng.best_bid()
            ask = eng.best_ask()
            spread_ticks = (ask[0] - bid[0]) if (bid and ask) else 4

            sigma_ticks = float(np.std(recent_returns[-50:])) * mid_ticks if len(recent_returns) >= 10 else 5.0
            sigma_ticks = max(sigma_ticks, 0.5)

            if is_avellaneda_stoikov:
                bids, asks = eng.depth(10)
                depth_map = {abs(l.px - mid_ticks): float(l.qty) for l in bids + asks if l.px != mid_ticks}
                time_remaining = max(1e-3, 1.0 - t / steps)
                maker.refresh_quotes(eng, mid_ticks, sigma_ticks, time_remaining, depth_map)
            else:
                vol_estimate = float(np.std(recent_returns[-50:])) if len(recent_returns) >= 10 else 0.0
                maker.refresh_quotes(eng, mid_ticks, vol_estimate)

            for nt in noise_traders:
                if rng.random() < 0.3:
                    res = nt.act(eng, mid_ticks, spread_ticks)
                    n_fills += _route(res, maker, maker_ids)

            for it in informed_traders:
                if rng.random() < 0.5:
                    res = it.act(eng, fundamental.value, mid_ticks if bid and ask else None)
                    n_fills += _route(res, maker, maker_ids)

            new_mid = eng.mid()
            if new_mid is not None:
                if last_mid is not None and last_mid > 0:
                    recent_returns.append(np.log(new_mid / last_mid))
                last_mid = new_mid
                last_known_mid_ticks = int(round(new_mid))
            mark_ticks = last_known_mid_ticks

            inventory_path[t] = maker.inventory
            pnl_path[t] = maker.mark_to_market(mark_ticks)

    d_pnl = np.diff(pnl_path)
    return MakerRunResult(
        label="", pnl_path=pnl_path, inventory_path=inventory_path, n_fills=n_fills,
        final_pnl=float(pnl_path[-1]), pnl_std=float(d_pnl.std()),
        sharpe_like=float(d_pnl.mean() / d_pnl.std()) if d_pnl.std() > 0 else 0.0,
        max_abs_inventory=int(np.abs(inventory_path).max()),
        mean_abs_inventory=float(np.abs(inventory_path).mean()),
    )


def _route(result, maker, maker_ids: set[int]) -> int:
    if result is None:
        return 0
    n = 0
    for f in result.fills:
        maker_owner = owner_of_order_id(f.maker_id)
        taker_owner = owner_of_order_id(f.taker_id)
        if maker_owner in maker_ids:
            maker.on_fill("sell" if f.taker_side == "buy" else "buy", f.qty, f.px)
            n += 1
        elif taker_owner in maker_ids:
            maker.on_fill(f.taker_side, f.qty, f.px)
            n += 1
    return n


def compare(
    *,
    steps: int = 3000,
    tick_size: float = 0.01,
    s0: float = 100.0,
    fundamental_sigma: float = 0.25,
    n_noise_traders: int = 20,
    n_informed_traders: int = 5,
    seed: int = 0,
) -> tuple[MakerRunResult, MakerRunResult]:
    heuristic = MarketMaker(trader_id=1, tick_size=tick_size)
    as_maker = AvellanedaStoikovMaker(trader_id=1, tick_size=tick_size,
                                       params=AvellanedaStoikovParams(gamma=0.1, k=1.5))

    common = dict(steps=steps, tick_size=tick_size, s0=s0, fundamental_sigma=fundamental_sigma,
                  n_noise_traders=n_noise_traders, n_informed_traders=n_informed_traders, seed=seed)

    r_heur = _run_one_maker(heuristic, is_avellaneda_stoikov=False, **common)
    r_as = _run_one_maker(as_maker, is_avellaneda_stoikov=True, **common)
    r_heur.label, r_as.label = "heuristic", "avellaneda-stoikov"
    return r_heur, r_as
