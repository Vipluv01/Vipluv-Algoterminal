"""Runs a strategy across many independently-seeded price paths and turns
the resulting distribution of outcomes into a confidence interval, rather
than reporting one backtest's Sharpe as if it were a fact about the
strategy.

This is the module that replaces every hand-typed strategy performance
number in the app: a strategy with no completed Monte Carlo run has no
Sharpe to show at all (see scripts/run_backtests.py and
app/models/backtest.py) -- there is no code path left that lets a
plausible-looking number reach a screen without this having produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.backtest.adapters import BacktestStrategy
from app.backtest.engine import BacktestRunResult, run_backtest
from app.backtest.paths import get_market_paths

BOOTSTRAP_RESAMPLES = 1000
CI_LOW_PERCENTILE = 5.0
CI_HIGH_PERCENTILE = 95.0


@dataclass(frozen=True)
class StrategyMetrics:
    strategy_key: str
    n_paths: int
    n_bars: int
    base_seed: int
    sharpe_median: float | None
    sharpe_ci_low: float | None    # 5th percentile
    sharpe_ci_high: float | None   # 95th percentile
    win_rate: float | None
    max_drawdown: float
    profit_factor: float | None
    calmar_ratio: float | None
    # Two DELIBERATELY separate counts -- see BacktestRunResult's own
    # comment. A strategy holding one open position the entire path
    # submits real orders but realizes zero round trips; reporting a
    # single "total_trades" would erase that distinction and make a
    # win-rate/profit-factor ratio look like it was computed over zero
    # trades when real orders (and real equity movement) actually happened.
    orders_submitted: int
    round_trips_closed: int
    per_path_results: tuple[BacktestRunResult, ...]
    # True when this strategy's own configuration makes the sweep
    # structurally invalid at this n_bars (see run_monte_carlo's
    # insufficient_horizon check) -- every other field above is a
    # placeholder (None/0) when this is True, and per_path_results is
    # empty: the backtest was never even attempted, not attempted and
    # found empty.
    skipped: bool = False
    skip_reason: str | None = None
    # How many of the n_paths produced a VALID (non-degenerate) sharpe/
    # calmar, and why the rest were excluded -- so "sharpe_median is None"
    # is explainable from the result object itself, not just silently
    # missing. See app.backtest.engine.MAX_PLAUSIBLE_ANNUALIZED_RATIO.
    n_sharpe_valid_paths: int = 0
    sharpe_invalid_reasons: tuple[str, ...] = field(default_factory=tuple)
    n_calmar_valid_paths: int = 0
    calmar_invalid_reasons: tuple[str, ...] = field(default_factory=tuple)


def _bootstrap_sharpe_ci(per_path_sharpe: np.ndarray, seed: int) -> tuple[float, float, float]:
    """Bootstraps a confidence interval for the MEDIAN Sharpe across paths
    by resampling-with-replacement from the per-path Sharpe values
    themselves, not from any single path's bar-level returns.

    This is deliberately a bootstrap over PATHS, not over one path's
    returns: the actual object of interest here is "how much does this
    strategy's Sharpe vary across different plausible market histories,"
    which is a question about path-to-path variance, and n_paths
    (typically 30) independent draws is what the bootstrap resamples --
    the same paired-comparison discipline generate_market_paths' own
    caching exists to support (every strategy scored on the identical set
    of paths, so a difference is attributable to the strategy).
    """
    rng = np.random.default_rng(seed)
    n = len(per_path_sharpe)
    resampled_medians = np.empty(BOOTSTRAP_RESAMPLES)
    for i in range(BOOTSTRAP_RESAMPLES):
        sample = rng.choice(per_path_sharpe, size=n, replace=True)
        resampled_medians[i] = np.median(sample)
    ci_low = float(np.percentile(resampled_medians, CI_LOW_PERCENTILE))
    ci_high = float(np.percentile(resampled_medians, CI_HIGH_PERCENTILE))
    return float(np.median(per_path_sharpe)), ci_low, ci_high


def run_monte_carlo(
    strategy: BacktestStrategy, n_paths: int = 30, n_bars: int = 2000, base_seed: int = 0,
) -> StrategyMetrics:
    """Evaluates `strategy` across n_paths independently-seeded market
    histories (seeds base_seed, base_seed+1, ..., base_seed+n_paths-1),
    each drawn from the SHARED path cache (paths.py) so running this for
    every registered strategy, one after another, generates each distinct
    (n_bars, seed) combination only once in total, not once per strategy.

    `strategy` is reused across all n_paths runs -- strategy.reset() is
    called before each one specifically so a stateful adapter
    (PairsAdapter/BasketAdapter) starts every path flat, not still holding
    whatever position the previous path happened to end on.
    """
    # hold_bars, when the strategy/adapter declares one (currently only
    # OptionsBacktestAdapter), is that strategy's OWN holding period in
    # bars. If it's at least as long as the whole path, the position can
    # NEVER close within any path -- every metric would be computed from
    # a single eternally-open position's unrealized drift, not a real
    # trading result (this is exactly what produced delta_neutral's
    # Sharpe=+2667 CI before this guard existed: hold_bars=500 against a
    # --bars 500 sweep). Skipped entirely, not run-and-discarded: there is
    # no valid computation to attempt here at all.
    hold_bars = getattr(strategy, "hold_bars", None)
    if hold_bars is not None and hold_bars >= n_bars:
        reason = (
            f"hold_bars({hold_bars}) >= n_bars({n_bars}): this strategy's own holding period "
            f"is at least as long as the entire backtest horizon, so its position can never "
            f"close within any path -- skipped rather than scored on an eternally-open "
            f"position's unrealized drift."
        )
        return StrategyMetrics(
            strategy_key=strategy.key, n_paths=n_paths, n_bars=n_bars, base_seed=base_seed,
            sharpe_median=None, sharpe_ci_low=None, sharpe_ci_high=None,
            win_rate=None, max_drawdown=0.0, profit_factor=None, calmar_ratio=None,
            orders_submitted=0, round_trips_closed=0, per_path_results=(), skipped=True, skip_reason=reason,
        )

    per_path_results: list[BacktestRunResult] = []
    for i in range(n_paths):
        seed = base_seed + i
        path = get_market_paths(steps=n_bars, seed=seed)
        strategy.reset()
        per_path_results.append(run_backtest(strategy, path))

    sharpe_values = [r.sharpe_ratio for r in per_path_results if r.sharpe_ratio is not None]
    sharpe_invalid_reasons = tuple(
        r.sharpe_invalid_reason for r in per_path_results if r.sharpe_invalid_reason is not None
    )
    if sharpe_values:
        sharpe_median, sharpe_ci_low, sharpe_ci_high = _bootstrap_sharpe_ci(np.array(sharpe_values), seed=base_seed)
    else:
        sharpe_median = sharpe_ci_low = sharpe_ci_high = None

    calmar_values = [r.calmar_ratio for r in per_path_results if r.calmar_ratio is not None]
    calmar_invalid_reasons = tuple(
        r.calmar_invalid_reason for r in per_path_results if r.calmar_invalid_reason is not None
    )
    calmar_ratio = float(np.mean(calmar_values)) if calmar_values else None

    win_rates = [r.win_rate for r in per_path_results if r.win_rate is not None]
    profit_factors = [r.profit_factor for r in per_path_results if r.profit_factor is not None]

    return StrategyMetrics(
        strategy_key=strategy.key,
        n_paths=n_paths,
        n_bars=n_bars,
        base_seed=base_seed,
        sharpe_median=sharpe_median,
        sharpe_ci_low=sharpe_ci_low,
        sharpe_ci_high=sharpe_ci_high,
        win_rate=float(np.mean(win_rates)) if win_rates else None,
        max_drawdown=float(np.mean([r.max_drawdown for r in per_path_results])),
        profit_factor=float(np.mean(profit_factors)) if profit_factors else None,
        calmar_ratio=calmar_ratio,
        orders_submitted=sum(r.orders_submitted for r in per_path_results),
        round_trips_closed=sum(r.round_trips_closed for r in per_path_results),
        per_path_results=tuple(per_path_results),
        n_sharpe_valid_paths=len(sharpe_values),
        sharpe_invalid_reasons=sharpe_invalid_reasons,
        n_calmar_valid_paths=len(calmar_values),
        calmar_invalid_reasons=calmar_invalid_reasons,
    )
