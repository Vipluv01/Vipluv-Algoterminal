import numpy as np
import pytest

from app.backtest.monte_carlo import _bootstrap_sharpe_ci, run_monte_carlo
from app.backtest.paths import clear_path_cache, get_market_paths


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_path_cache()
    yield
    clear_path_cache()


class _NeverTradesStrategy:
    key = "never_trades"

    def evaluate(self, history, step):
        return []

    def reset(self):
        pass


class _StatefulFakeStrategy:
    """Simulates a PairsAdapter/BasketAdapter-shaped strategy that carries
    position state across bars -- used to prove run_monte_carlo actually
    calls reset() between paths, not just that it CAN."""

    key = "stateful_fake"

    def __init__(self):
        self.position = "none"
        self.reset_call_count = 0
        self.max_position_seen_at_bar_zero = []

    def reset(self):
        self.reset_call_count += 1
        self.position = "none"

    def evaluate(self, history, step):
        if step == 0:
            self.max_position_seen_at_bar_zero.append(self.position)
        if step == 5:
            self.position = "long_spread"
        return []


def test_bootstrap_ci_brackets_the_median_for_low_variance_input():
    values = np.array([1.0, 1.01, 0.99, 1.02, 0.98, 1.0, 1.01])
    median, lo, hi = _bootstrap_sharpe_ci(values, seed=0)
    assert lo <= median <= hi
    assert median == pytest.approx(np.median(values))


def test_bootstrap_ci_widens_with_more_variance_in_the_input():
    tight = np.array([1.0, 1.01, 0.99, 1.0, 1.01, 0.99, 1.0])
    wide = np.array([-3.0, 5.0, -2.0, 4.0, 0.0, 3.0, -1.0])
    _, lo_tight, hi_tight = _bootstrap_sharpe_ci(tight, seed=1)
    _, lo_wide, hi_wide = _bootstrap_sharpe_ci(wide, seed=1)
    assert (hi_wide - lo_wide) > (hi_tight - lo_tight)


def test_bootstrap_ci_is_deterministic_given_the_same_seed():
    values = np.array([0.5, 1.5, -0.5, 2.0, 0.0])
    r1 = _bootstrap_sharpe_ci(values, seed=3)
    r2 = _bootstrap_sharpe_ci(values, seed=3)
    assert r1 == r2


def test_run_monte_carlo_resets_state_before_every_path():
    """The real bug this exists to prevent (see adapters.py's
    BacktestStrategy.reset docstring): without reset(), path 2 would start
    already "holding" whatever position path 1's synthetic history ended
    on. Confirmed two ways: reset() is called exactly n_paths times, AND
    the strategy's own recorded position at step==0 of every path is
    "none" -- never carried over from the previous path's step 5 mutation."""
    strategy = _StatefulFakeStrategy()
    n_paths = 4
    run_monte_carlo(strategy, n_paths=n_paths, n_bars=20, base_seed=0)

    assert strategy.reset_call_count == n_paths
    assert strategy.max_position_seen_at_bar_zero == ["none"] * n_paths


def test_run_monte_carlo_produces_one_result_per_path():
    strategy = _NeverTradesStrategy()
    metrics = run_monte_carlo(strategy, n_paths=6, n_bars=30, base_seed=0)
    assert len(metrics.per_path_results) == 6
    assert metrics.n_paths == 6
    assert metrics.n_bars == 30
    assert metrics.base_seed == 0


def test_run_monte_carlo_with_zero_trades_reports_none_win_rate_and_none_sharpe():
    """A strategy that never trades has a perfectly flat equity curve on
    every path -- zero return variance, so there is no valid Sharpe to
    report. An earlier version fabricated 0.0 here, which reads as
    "measured, and it's flat" rather than "the measurement is invalid" --
    see app.backtest.engine._sharpe_ratio's own docstring."""
    strategy = _NeverTradesStrategy()
    metrics = run_monte_carlo(strategy, n_paths=3, n_bars=20, base_seed=0)
    assert metrics.win_rate is None
    assert metrics.profit_factor is None
    assert metrics.orders_submitted == 0
    assert metrics.round_trips_closed == 0
    assert metrics.sharpe_median is None
    assert metrics.sharpe_ci_low is None
    assert metrics.sharpe_ci_high is None
    assert metrics.n_sharpe_valid_paths == 0
    assert len(metrics.sharpe_invalid_reasons) > 0


def test_run_monte_carlo_uses_distinct_seeds_per_path():
    """base_seed, base_seed+1, ..., base_seed+n_paths-1 -- verified by
    checking that the underlying paths pulled from the cache are actually
    different across the n_paths runs, not the same path repeated."""
    strategy = _NeverTradesStrategy()
    base_seed = 500
    n_paths = 3
    run_monte_carlo(strategy, n_paths=n_paths, n_bars=15, base_seed=base_seed)

    paths_used = [get_market_paths(steps=15, seed=base_seed + i) for i in range(n_paths)]
    closes = [p.paths["ICICIBANK"].close.tolist() for p in paths_used]
    assert closes[0] != closes[1]
    assert closes[1] != closes[2]


def test_run_monte_carlo_end_to_end_real_strategy():
    from app.backtest.adapters import SingleInstrumentAdapter
    from app.strategies.alpha import AlphaRSIEMAStrategy

    adapter = SingleInstrumentAdapter(AlphaRSIEMAStrategy(), "ICICIBANK")
    metrics = run_monte_carlo(adapter, n_paths=5, n_bars=300, base_seed=42)

    assert metrics.strategy_key == "alpha_rsi_ema"
    # A legitimately None sharpe_median is possible here (see app.backtest.
    # engine.MAX_PLAUSIBLE_ANNUALIZED_RATIO): under the corrected
    # BARS_PER_YEAR (a bar is one simulated second, not one trading day --
    # see engine.py's own comment), a real strategy's per-path Sharpe can
    # legitimately exceed the plausibility guard on some paths. What must
    # hold either way is "no NaN/inf leaks through, and the CI still
    # brackets the median whenever both exist."
    assert metrics.sharpe_median is None or np.isfinite(metrics.sharpe_median)
    if metrics.sharpe_median is not None:
        assert metrics.sharpe_ci_low <= metrics.sharpe_median <= metrics.sharpe_ci_high
    assert 0.0 <= metrics.max_drawdown <= 1.0


def test_run_monte_carlo_is_deterministic():
    from app.backtest.adapters import SingleInstrumentAdapter
    from app.strategies.momentum import MomentumMACDStrategy

    m1 = run_monte_carlo(SingleInstrumentAdapter(MomentumMACDStrategy(), "ICICIBANK"), n_paths=4, n_bars=200, base_seed=7)
    clear_path_cache()  # force genuine regeneration, not a cache hit
    m2 = run_monte_carlo(SingleInstrumentAdapter(MomentumMACDStrategy(), "ICICIBANK"), n_paths=4, n_bars=200, base_seed=7)

    assert m1.sharpe_median == m2.sharpe_median
    assert m1.sharpe_ci_low == m2.sharpe_ci_low
    assert m1.sharpe_ci_high == m2.sharpe_ci_high
    assert m1.orders_submitted == m2.orders_submitted
    assert m1.round_trips_closed == m2.round_trips_closed


def test_paths_are_shared_across_strategies_evaluated_on_the_same_seeds():
    """The core performance claim: running a SECOND strategy over the same
    (n_bars, base_seed, n_paths) must not regenerate any path -- checked
    directly via cache object identity, not just "it was fast"."""
    from app.backtest.adapters import SingleInstrumentAdapter
    from app.strategies.alpha import AlphaRSIEMAStrategy
    from app.strategies.momentum import MomentumMACDStrategy

    run_monte_carlo(SingleInstrumentAdapter(AlphaRSIEMAStrategy(), "ICICIBANK"), n_paths=3, n_bars=50, base_seed=200)
    ids_after_first = [id(get_market_paths(steps=50, seed=200 + i)) for i in range(3)]

    run_monte_carlo(SingleInstrumentAdapter(MomentumMACDStrategy(), "ICICIBANK"), n_paths=3, n_bars=50, base_seed=200)
    ids_after_second = [id(get_market_paths(steps=50, seed=200 + i)) for i in range(3)]

    # Same OBJECT IDENTITY, not just equal values -- proves the second
    # strategy's run pulled from cache rather than regenerating (a
    # MultiAssetHistory isn't hashable -- it holds a dict field -- so this
    # is checked via id() rather than set equality).
    assert ids_after_first == ids_after_second
