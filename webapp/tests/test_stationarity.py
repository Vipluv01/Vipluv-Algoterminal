import numpy as np
import pytest

from app.quant.stationarity import adf_test, half_life, hurst_exponent, johansen_test


# ---------------------------------------------------------------------------
# Hurst exponent
# ---------------------------------------------------------------------------

def test_hurst_of_a_random_walk_is_close_to_half():
    """A true random walk's increments are i.i.d. -- H should land near 0.5.
    Bias-corrected (see stationarity.py's _expected_rs_iid docstring for
    why the correction is necessary, not cosmetic: the uncorrected
    statistic reads systematically high at finite sample sizes)."""
    rng = np.random.default_rng(0)
    series = 100 + np.cumsum(rng.normal(0, 1, 6000))
    h = hurst_exponent(series)
    assert h == pytest.approx(0.5, abs=0.05)


def test_hurst_of_a_random_walk_is_close_to_half_across_seeds():
    """Same claim, checked over several seeds rather than trusting one --
    confirms the bias correction isn't accidentally tuned to a single
    lucky draw."""
    for seed in range(5):
        rng = np.random.default_rng(seed)
        series = 100 + np.cumsum(rng.normal(0, 1, 6000))
        h = hurst_exponent(series)
        assert h == pytest.approx(0.5, abs=0.06), f"seed {seed}: H={h}"


def test_hurst_of_a_mean_reverting_series_is_below_threshold():
    """A stationary AR(1) with 0 < phi < 1 on the LEVEL (mean-reverting
    toward 0) has anti-persistent increments -- H well below 0.5."""
    rng = np.random.default_rng(0)
    n = 4000
    series = np.zeros(n)
    for t in range(1, n):
        series[t] = 0.3 * series[t - 1] + rng.normal(0, 1)
    assert hurst_exponent(series) < 0.45


def test_hurst_of_a_trending_series_is_above_threshold():
    """Momentum: increments are themselves positively autocorrelated
    (phi=0.7 on the DIFFERENCE series, not on the level), giving the price
    path persistent, trending behaviour -- H clearly above 0.5.

    Note a random walk with a constant additive drift is NOT a valid test
    case here: adding a deterministic drift to i.i.d. increments changes
    the mean, not the variance-scaling Hurst measures, so it still reads
    H~0.5 (verified directly). Genuine trending requires autocorrelated
    increments, i.e. actual momentum, not just a directional walk.
    """
    rng = np.random.default_rng(0)
    n = 6000
    increments = np.zeros(n)
    for t in range(1, n):
        increments[t] = 0.7 * increments[t - 1] + rng.normal(0, 1)
    series = np.cumsum(increments)
    assert hurst_exponent(series) > 0.55


def test_hurst_raises_on_too_short_a_series():
    with pytest.raises(ValueError):
        hurst_exponent(np.arange(10, dtype=float))


# ---------------------------------------------------------------------------
# ADF
# ---------------------------------------------------------------------------

def test_adf_rejects_unit_root_for_a_cointegrated_pairs_spread():
    """The residual of a genuinely cointegrated pair is stationary by
    construction -- ADF must reject the unit-root null with a tiny
    p-value, not sit on the fence."""
    rng = np.random.default_rng(0)
    n = 3000
    b = 100 + np.cumsum(rng.normal(0, 0.5, n))
    a = 1.5 * b + 5 + rng.normal(0, 0.3, n)
    spread = a - 1.5 * b - 5

    result = adf_test(spread)
    assert result.is_stationary
    assert result.pvalue < 0.001
    assert set(result.critical_values) == {"1%", "5%", "10%"}


def test_adf_fails_to_reject_unit_root_for_independent_random_walks():
    """The naive difference of two INDEPENDENT random walks is itself a
    random walk (still has a unit root) -- ADF must NOT claim stationarity
    here. This is the negative control matching the positive one above."""
    rng = np.random.default_rng(0)
    n = 3000
    a = 100 + np.cumsum(rng.normal(0, 0.5, n))
    b = 200 + np.cumsum(rng.normal(0, 0.5, n))

    result = adf_test(a - b)
    assert not result.is_stationary
    assert result.pvalue > 0.10


def test_adf_usedlag_and_stat_are_populated():
    rng = np.random.default_rng(0)
    series = np.zeros(500)
    for t in range(1, 500):
        series[t] = 0.3 * series[t - 1] + rng.normal(0, 1)
    result = adf_test(series)
    assert result.usedlag >= 0
    assert isinstance(result.stat, float)


# ---------------------------------------------------------------------------
# Johansen
# ---------------------------------------------------------------------------

def test_johansen_finds_rank_one_for_a_cointegrated_pair():
    """A genuinely cointegrated pair: the rank<=0 null should be rejected
    (r_0_passed True -- at least one cointegrating relationship exists),
    but rank<=1 should NOT be rejected (r_1_passed False -- rank is
    exactly 1, not the full 2, which would mean both series are
    individually stationary rather than only their combination)."""
    rng = np.random.default_rng(0)
    n = 3000
    b = 100 + np.cumsum(rng.normal(0, 0.5, n))
    a = 1.5 * b + 5 + rng.normal(0, 0.3, n)

    result = johansen_test(a, b)
    assert result.r_0_passed is True
    assert result.r_1_passed is False
    assert len(result.trace_stats) == 2
    assert len(result.critical_values_90) == len(result.critical_values_95) == len(result.critical_values_99) == 2
    # Trace stat for rank<=0 must comfortably clear even the 99% critical value.
    assert result.trace_stats[0] > result.critical_values_99[0]


def test_johansen_finds_no_cointegration_for_independent_random_walks():
    """Two independent random walks: the rank<=0 null should NOT be
    rejected -- there is no evidence of any cointegrating relationship."""
    rng = np.random.default_rng(0)
    n = 3000
    a = 100 + np.cumsum(rng.normal(0, 0.5, n))
    b = 200 + np.cumsum(rng.normal(0, 0.5, n))

    result = johansen_test(a, b)
    assert result.r_0_passed is False


def test_johansen_critical_values_increase_with_confidence():
    """90% < 95% < 99% critical value at each rank hypothesis -- a basic
    sanity check on the values statsmodels returned being wired to the
    right columns in the right order."""
    rng = np.random.default_rng(0)
    n = 500
    a = 100 + np.cumsum(rng.normal(0, 0.5, n))
    b = 200 + np.cumsum(rng.normal(0, 0.5, n))
    result = johansen_test(a, b)
    for i in range(2):
        assert result.critical_values_90[i] < result.critical_values_95[i] < result.critical_values_99[i]


# ---------------------------------------------------------------------------
# Half-life
# ---------------------------------------------------------------------------

def test_half_life_recovers_the_true_ou_parameter():
    """Simulate a genuine discrete-time OU process with a known lambda and
    confirm the OLS-fitted half-life is close to ln(2)/lambda."""
    rng = np.random.default_rng(0)
    n = 3000
    lam_true = 0.1
    mu = 50.0
    s = np.empty(n)
    s[0] = mu
    for t in range(1, n):
        s[t] = s[t - 1] + lam_true * (mu - s[t - 1]) + rng.normal(0, 1)

    true_hl = np.log(2) / lam_true
    assert half_life(s) == pytest.approx(true_hl, rel=0.1)


def test_half_life_is_shorter_for_faster_mean_reversion():
    """A larger lambda (faster reversion) must yield a SHORTER half-life --
    monotonicity check using two OU processes built from the same seed."""
    n = 3000
    mu = 50.0

    def simulate(lam, seed):
        rng = np.random.default_rng(seed)
        s = np.empty(n)
        s[0] = mu
        for t in range(1, n):
            s[t] = s[t - 1] + lam * (mu - s[t - 1]) + rng.normal(0, 1)
        return s

    slow = simulate(0.05, seed=1)
    fast = simulate(0.3, seed=1)
    assert half_life(fast) < half_life(slow)


def test_half_life_is_effectively_infinite_for_a_random_walk():
    """A pure random walk's TRUE population slope on lagged level is
    exactly 0 -- but any single OLS estimate has sampling noise around
    that true zero, so the fitted slope lands slightly positive about as
    often as slightly negative (verified directly: across 10 seeds at
    n=3000, none came back as literal +inf, all landed as large finite
    values from ~200 to ~5000 bars). Asserting exact `== inf` would make
    this test pass or fail on a coin flip. The honest claim is "no
    plausible short mean-reversion window," checked across several seeds
    so one lucky/unlucky draw can't decide the result -- every seed here
    must clear a threshold far past any realistic mean-reverting half-life
    (the OU tests above use lambda up to 0.3, half-life ~2.3 bars)."""
    for seed in range(5):
        rng = np.random.default_rng(seed)
        series = 100 + np.cumsum(rng.normal(0, 1, 3000))
        result = half_life(series)
        assert result == float("inf") or result > 100, f"seed {seed}: half_life={result}"
