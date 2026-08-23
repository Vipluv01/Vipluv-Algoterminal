import numpy as np
import pytest

from app.portfolio_optimizer import max_sharpe_allocation


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
