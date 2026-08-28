"""Headless price-path generation for backtesting, and the cache that makes
running 8 strategies' Monte Carlo sweeps affordable.

Why caching is load-bearing, not a nicety: `SymbolMarket.step()` is not one
IPC round trip to the Go engine, it's ~17 (three book queries plus one
`submit` per acting agent) -- measured directly at ~220us/bar/symbol. A
single (steps=2000, 7-symbol) path therefore costs several seconds to
generate. Regenerating it once per strategy (8 strategies) rather than once
per (steps, seed) pair would multiply that by 8 for zero benefit, since the
market simulation itself has NOTHING strategy-specific in it -- price
paths are generated once and every strategy is backtested against the
identical path, which is also what makes cross-strategy Sharpe comparisons
paired rather than each drawing separately from path-selection luck.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.markets import MarketRegistry


@dataclass(frozen=True)
class AssetPath:
    """One symbol's simulated bar series.

    open/high/low/close are all derived from a single price OBSERVATION
    per bar (SymbolMarket.step()'s returned mark), not real intrabar tick
    data -- this simulation has one price per step, not a sequence of
    trades within it. open[t] = close[t-1] (the seed price for t=0), and
    high/low are just the wider of the two, honestly reflecting "no
    intrabar excursion was observed" rather than fabricating a wick.
    Capturing genuine intrabar highs/lows would need additional book
    queries per bar, directly working against the IPC-cost concern this
    module exists to solve -- so this is a deliberate, documented
    simplification, not an oversight.
    """

    symbol: str
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray


@dataclass(frozen=True)
class MultiAssetHistory:
    steps: int
    seed: int
    symbols: tuple[str, ...]
    paths: dict[str, AssetPath]

    def close_series(self, symbol: str, upto: int | None = None) -> np.ndarray:
        """Close prices for `symbol`, optionally truncated to bars [0, upto)
        -- the slice a strategy evaluating bar `upto` is allowed to see
        (no look-ahead past the bar currently being evaluated)."""
        closes = self.paths[symbol].close
        return closes if upto is None else closes[:upto]


def generate_market_paths(steps: int = 2000, seed: int = 42) -> MultiAssetHistory:
    """Runs a fresh MarketRegistry headlessly for `steps` bars.

    "Headless" here just means calling registry.step_all() in a tight
    loop rather than going through app/main.py's async _tick_loop -- that
    loop's only non-simulation behavior is `await asyncio.sleep(1.0)`
    (real-time pacing) and broadcasting over the live WebSocket, neither
    of which markets.py itself does or needs; step_all() alone already IS
    the complete headless simulation step, so there is nothing async-loop-
    specific to bypass beyond simply not calling it.

    Deterministic given (steps, seed): MarketRegistry seeds each
    SymbolMarket's own np.random.default_rng(seed + i) independently (see
    app/markets.py), which does not depend on any global random state, and
    the Go engine subprocess is driven by a synchronous, single-threaded
    request/response pipe with no concurrency to reorder -- verified
    directly in tests/test_paths.py by generating the same (steps, seed)
    twice from two entirely separate MarketRegistry instances and
    comparing element-for-element.
    """
    registry = MarketRegistry(seed=seed)
    try:
        symbols = tuple(registry.markets.keys())
        prev_close = {sym: registry.markets[sym].current_price for sym in symbols}
        opens: dict[str, list[float]] = {sym: [] for sym in symbols}
        highs: dict[str, list[float]] = {sym: [] for sym in symbols}
        lows: dict[str, list[float]] = {sym: [] for sym in symbols}
        closes: dict[str, list[float]] = {sym: [] for sym in symbols}
        volumes: dict[str, list[float]] = {sym: [] for sym in symbols}

        for _ in range(steps):
            registry.step_all()
            for sym in symbols:
                m = registry.markets[sym]
                o = prev_close[sym]
                c = m.current_price
                opens[sym].append(o)
                closes[sym].append(c)
                highs[sym].append(max(o, c))
                lows[sym].append(min(o, c))
                # recent_volume[-1] is this step's traded volume, recorded
                # by SymbolMarket's own fill-tracking wrapper at zero extra
                # IPC cost (Phase 1) -- not re-derived here.
                volumes[sym].append(float(m.recent_volume[-1]) if m.recent_volume else 0.0)
                prev_close[sym] = c
    finally:
        registry.close()

    paths = {
        sym: AssetPath(
            symbol=sym,
            open=np.array(opens[sym]),
            high=np.array(highs[sym]),
            low=np.array(lows[sym]),
            close=np.array(closes[sym]),
            volume=np.array(volumes[sym]),
        )
        for sym in symbols
    }
    return MultiAssetHistory(steps=steps, seed=seed, symbols=symbols, paths=paths)


@lru_cache(maxsize=None)
def _cached_generate_market_paths(steps: int, seed: int) -> MultiAssetHistory:
    return generate_market_paths(steps=steps, seed=seed)


def get_market_paths(steps: int = 2000, seed: int = 42) -> MultiAssetHistory:
    """The cached accessor -- what run_monte_carlo and the CLI sweep across
    all registered strategies should call, so a given (steps, seed) is
    only ever generated once per process. generate_market_paths itself
    stays uncached (a plain function, not memoized) for anyone who
    explicitly wants a fresh, independent run -- e.g. the determinism test
    that generates the same (steps, seed) twice specifically to compare
    two independently-produced results, which a cache would defeat by
    definition.
    """
    return _cached_generate_market_paths(steps, seed)


def clear_path_cache() -> None:
    """Test isolation: each test that exercises the cache should start
    from an empty one, not see whatever a previous test already populated
    it with."""
    _cached_generate_market_paths.cache_clear()
