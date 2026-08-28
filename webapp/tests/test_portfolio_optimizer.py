import numpy as np
import pytest

from app.portfolio_optimizer import (
    _random_search_allocation,
    _slsqp_allocation,
    _validate_and_prepare,
    max_sharpe_allocation,
)


def test_weights_sum_to_one_and_are_non_negative():
    rng = np.random.default_rng(0)
    returns = {
        "a": rng.normal(0.001, 0.01, 500),
        "b": rng.normal(0.0005, 0.02, 500),
        "c": rng.normal(0.0008, 0.015, 500),
    }
    result = max_sharpe_allocation(returns, n_random_portfolios=5000)
    assert result.weights.sum() == pytest.approx(1.0, abs=1e-9)
    assert (result.weights >= 0).all()


def test_favors_the_strategy_with_a_clearly_better_risk_adjusted_return():
    rng = np.random.default_rng(1)
    n = 1000
    # "good": steady positive drift, low noise. "bad": near-zero drift, high noise.
    good = rng.normal(0.002, 0.005, n)
    bad = rng.normal(0.0001, 0.03, n)
    result = max_sharpe_allocation({"good": good, "bad": bad}, n_random_portfolios=20_000)
    good_weight = result.weights[result.strategy_keys.index("good")]
    bad_weight = result.weights[result.strategy_keys.index("bad")]
    assert good_weight > bad_weight


def test_identical_strategies_produce_the_same_sharpe_as_holding_either_alone():
    """Two PERFECTLY identical return streams are a degenerate case for
    mean-variance optimization: every split between them (90/10, 50/50,
    10/90, ...) produces the exact same portfolio return and variance,
    since it's the same underlying series either way. The optimizer is
    mathematically indifferent between them, so which one a random search
    happens to land on is arbitrary -- NOT something to assert on. What
    IS guaranteed: combining two identical assets can't beat (or lose to)
    holding either one alone, so the resulting Sharpe ratio must match a
    single strategy's own Sharpe, not some inflated diversification
    benefit that doesn't actually exist here.
    """
    rng = np.random.default_rng(2)
    shared = rng.normal(0.001, 0.01, 800)
    result = max_sharpe_allocation({"x": shared, "y": shared.copy()}, n_random_portfolios=20_000)

    solo_return = shared.mean() * 252
    solo_vol = shared.std(ddof=0) * (252 ** 0.5)
    solo_sharpe = solo_return / solo_vol

    assert result.sharpe_ratio == pytest.approx(solo_sharpe, rel=1e-2)


def test_rejects_mismatched_length_series():
    with pytest.raises(ValueError):
        max_sharpe_allocation({"a": np.zeros(100), "b": np.zeros(50)})


def test_rejects_fewer_than_two_strategies():
    with pytest.raises(ValueError):
        max_sharpe_allocation({"a": np.zeros(100)})


def test_sharpe_ratio_is_consistent_with_return_and_volatility():
    rng = np.random.default_rng(3)
    returns = {"a": rng.normal(0.001, 0.01, 500), "b": rng.normal(0.0008, 0.012, 500)}
    result = max_sharpe_allocation(returns, risk_free_rate=0.0, n_random_portfolios=5000)
    recomputed = result.expected_return / result.expected_volatility
    assert result.sharpe_ratio == pytest.approx(recomputed, rel=1e-6)


# ---------------------------------------------------------------------------
# SLSQP vs. random search: the actual claim behind the upgrade
# ---------------------------------------------------------------------------

def _slsqp_and_random_search_sharpe(returns: dict[str, np.ndarray], seed: int) -> tuple[float, float]:
    """Runs both solvers independently over the SAME inputs and returns
    their Sharpe ratios -- not through max_sharpe_allocation (which only
    ever runs SLSQP now), so this is a genuine head-to-head, not a
    self-comparison."""
    _keys, mean_p, cov_p = _validate_and_prepare(returns)
    _w, _r, _v, slsqp_sharpe = _slsqp_allocation(mean_p, cov_p, risk_free_rate=0.0, periods_per_year=252)
    _w, _r, _v, rs_sharpe = _random_search_allocation(
        mean_p, cov_p, risk_free_rate=0.0, periods_per_year=252, n_random_portfolios=20_000, seed=seed,
    )
    return slsqp_sharpe, rs_sharpe


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_slsqp_sharpe_is_never_worse_than_random_search(seed):
    """The claim the upgrade is actually FOR: a real gradient solver
    should find an allocation at least as good as a 20,000-sample random
    search, not just a differently-flavored answer. Checked across several
    seeds and asset counts rather than one lucky draw, with a small
    tolerance (1e-4) for the case where random search happens to land
    almost exactly on the true optimum by chance."""
    rng = np.random.default_rng(seed)
    n_assets = 3 + (seed % 3)  # varies 3..5 assets across seeds
    returns = {
        f"strategy_{i}": rng.normal(rng.uniform(-0.001, 0.002), rng.uniform(0.005, 0.03), 600)
        for i in range(n_assets)
    }
    slsqp_sharpe, rs_sharpe = _slsqp_and_random_search_sharpe(returns, seed)
    assert slsqp_sharpe >= rs_sharpe - 1e-4, (
        f"seed={seed}: SLSQP={slsqp_sharpe} worse than random search={rs_sharpe} "
        f"by more than the 1e-4 tolerance"
    )


def test_slsqp_meaningfully_beats_random_search_on_a_clear_case():
    """Not just "not worse" -- on a case with a clear, findable optimum
    (two good-Sharpe assets, two much worse ones), SLSQP's gradient search
    should land closer to the true corner solution than 20,000 random
    Dirichlet draws, which spend most of their mass on the interior of the
    simplex rather than near a sparse optimum."""
    rng = np.random.default_rng(7)
    returns = {
        "good_a": rng.normal(0.0015, 0.008, 750),
        "good_b": rng.normal(0.0012, 0.009, 750),
        "bad_a": rng.normal(0.0001, 0.03, 750),
        "bad_b": rng.normal(-0.0002, 0.025, 750),
    }
    slsqp_sharpe, rs_sharpe = _slsqp_and_random_search_sharpe(returns, seed=7)
    assert slsqp_sharpe > rs_sharpe


def test_max_sharpe_allocation_matches_slsqp_allocation_directly():
    """The public function's result must be exactly what _slsqp_allocation
    alone produces -- max_sharpe_allocation should not be silently
    blending in the random search anywhere."""
    rng = np.random.default_rng(5)
    returns = {"a": rng.normal(0.001, 0.01, 400), "b": rng.normal(0.0007, 0.015, 400), "c": rng.normal(0.0005, 0.02, 400)}

    result = max_sharpe_allocation(returns)
    _keys, mean_p, cov_p = _validate_and_prepare(returns)
    w, ret, vol, sharpe = _slsqp_allocation(mean_p, cov_p, risk_free_rate=0.0, periods_per_year=252)

    assert result.sharpe_ratio == pytest.approx(sharpe, abs=1e-10)
    assert result.expected_return == pytest.approx(ret, abs=1e-10)
    assert result.expected_volatility == pytest.approx(vol, abs=1e-10)
    assert np.allclose(result.weights, w)
