"""Stationarity and cointegration test suite: pure statistical functions
over price/spread series, no strategy logic or API surface in this module.

app/strategies/pairs_cointegration.py already uses Engle-Granger
cointegration (statsmodels' `coint`) directly in the live trading path --
this module is the lower-level toolkit for everything ELSE that wants a
rigorous stationarity read: display/analytics pages wanting ADF, Johansen,
Hurst, or half-life alongside the strategy's own p-value, and Phase 3's
backtest harness, which needs to characterize a series without necessarily
trading it.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen

ADF_SIGNIFICANCE = 0.05
# statsmodels' cvt/cvm layout is columns [90%, 95%, 99%]; this is which
# column the boolean pass/fail flags below are read from. Exposed as a
# module constant, not hardcoded inline, since Phase 6's UI will want to
# label whichever significance level these flags actually reflect.
JOHANSEN_SIGNIFICANCE_COL = 1  # 95%


@dataclass(frozen=True)
class ADFResult:
    stat: float
    pvalue: float
    usedlag: int
    critical_values: dict[str, float]  # "1%", "5%", "10%"
    is_stationary: bool  # pvalue < ADF_SIGNIFICANCE


def adf_test(series: np.ndarray, maxlag: int | None = None) -> ADFResult:
    """Augmented Dickey-Fuller unit-root test.

    H0: the series has a unit root (is non-stationary / a random walk).
    A low p-value rejects H0 -- meaning the series (applied to a pair's
    spread: the pair) is stationary / mean-reverting, not that it is
    guaranteed to be, only that a random walk is an unlikely explanation
    for what was observed.
    """
    stat, pvalue, usedlag, _nobs, critical_values, _icbest = adfuller(
        np.asarray(series, dtype=float), maxlag=maxlag,
    )
    return ADFResult(
        stat=float(stat),
        pvalue=float(pvalue),
        usedlag=int(usedlag),
        critical_values={k: float(v) for k, v in critical_values.items()},
        is_stationary=bool(pvalue < ADF_SIGNIFICANCE),
    )


@dataclass(frozen=True)
class JohansenResult:
    """Johansen trace test for cointegration rank between two series.

    trace_stats / critical_values_* are indexed [rank<=0 hypothesis,
    rank<=1 hypothesis] -- the fixed order coint_johansen returns them in
    for a 2-series system.

    r_0_passed: the rank<=0 null is REJECTED at the 95% level, i.e. at
    least one cointegrating relationship exists between the two series.
    This is "cointegrated" in the sense a pairs trade cares about.

    r_1_passed: the rank<=1 null is REJECTED at the 95% level, i.e. the
    rank is a full 2 -- both series are stationary essentially on their
    own, not just in combination. For a genuine pairs-trade candidate
    (two individually non-stationary series with one stationary
    combination) this should normally be False; True here is a sign the
    inputs may not be the I(1)-non-stationary series Johansen assumes.
    """

    trace_stats: list[float]
    critical_values_90: list[float]
    critical_values_95: list[float]
    critical_values_99: list[float]
    r_0_passed: bool
    r_1_passed: bool


def johansen_test(y: np.ndarray, x: np.ndarray) -> JohansenResult:
    """Complements the Engle-Granger `coint()` test the live pairs strategy
    already uses: Johansen doesn't require choosing which series is
    "dependent," and directly estimates cointegrating RANK rather than
    just testing for the existence of one relationship -- at the cost of
    needing more data to be reliable, which is why it's offered as a
    separate display-level check rather than gating live trading.
    """
    endog = np.column_stack([np.asarray(y, dtype=float), np.asarray(x, dtype=float)])
    with warnings.catch_warnings():
        # statsmodels casts an internally-complex eigenvalue-computation
        # intermediate to real and warns about it on numpy >=2 -- the
        # discarded imaginary part is numerical noise from the eigenvalue
        # solver, not a real signal (verified directly against known
        # cointegrated/independent series in tests/test_stationarity.py:
        # the trace statistics match expected magnitudes either way).
        warnings.simplefilter("ignore", category=np.exceptions.ComplexWarning)
        result = coint_johansen(endog, det_order=0, k_ar_diff=1)

    trace_stats = [float(v) for v in result.lr1]
    cvt = result.cvt  # shape (2, 3): rows=[r<=0, r<=1], cols=[90%, 95%, 99%]
    return JohansenResult(
        trace_stats=trace_stats,
        critical_values_90=[float(cvt[0][0]), float(cvt[1][0])],
        critical_values_95=[float(cvt[0][1]), float(cvt[1][1])],
        critical_values_99=[float(cvt[0][2]), float(cvt[1][2])],
        r_0_passed=bool(trace_stats[0] > cvt[0][JOHANSEN_SIGNIFICANCE_COL]),
        r_1_passed=bool(trace_stats[1] > cvt[1][JOHANSEN_SIGNIFICANCE_COL]),
    )


def _expected_rs_iid(n: int) -> float:
    """Anis-Lloyd (1976) expected value of R/S under the null of i.i.d.
    increments, at window size n. Closed-form summation -- no external
    dependency needed, and cheap enough (O(n) per window) at the series
    lengths this app works with.

    This exists because the classical R/S statistic is a well-documented
    BIASED estimator at finite sample sizes (it systematically reads high
    for a true H=0.5 random walk -- verified directly: uncorrected R/S on
    6,000-bar synthetic random walks averaged H=0.53 across 20 seeds, with
    individual seeds as high as 0.59, well outside a naive H=0.5+-0.05
    tolerance). Anis-Lloyd is the standard correction (see also Peters,
    "Fractal Market Analysis," 1994) -- subtracting this expected value in
    log-space before the log(R/S) vs log(n) regression re-centers the
    estimator, not just widens the tolerance to hide the bias.
    """
    i = np.arange(1, n)
    return float(np.sum(np.sqrt((n - i) / i)) / np.sqrt(n * np.pi / 2))


def hurst_exponent(series: np.ndarray, max_lags: int = 20) -> float:
    """Hurst exponent via bias-corrected Rescaled Range (R/S) analysis.

    H < 0.5: mean-reverting.  H = 0.5: a random walk.  H > 0.5: trending /
    momentum (positively autocorrelated increments).

    Computed on the FIRST DIFFERENCES of `series`, not the raw levels.
    This is deliberate, not incidental: R/S theory assumes the input is
    already weakly stationary (Hurst's original 1951 formulation used
    annual river-discharge figures, not cumulative discharge). Applying it
    directly to price/spread LEVELS -- which are themselves an integrated,
    non-stationary series for anything except a perfectly flat one -- is a
    known misapplication that produces spurious H near 1.0 regardless of
    the true underlying process (verified directly: a plain random walk's
    price level, run through block R/S with no differencing, measured
    H~1.0, not the correct ~0.5). Differencing first is the standard fix,
    and is exactly what turns "the level series is a random walk" into
    "the increments are the i.i.d. noise R/S is meant to characterize."

    max_lags controls how many distinct window sizes are sampled
    (log-spaced from a small window up to a quarter of the series length),
    not literal lags 2..max_lags -- R/S needs whole sub-windows to compute
    a range and a standard deviation from, not single-lag differences.
    """
    series = np.asarray(series, dtype=float)
    increments = np.diff(series)
    n = len(increments)

    min_window = 10
    max_window = n // 4
    if max_window < min_window:
        raise ValueError(
            f"series too short for hurst_exponent: need at least "
            f"{4 * min_window + 1} points, got {len(series)}"
        )
    sizes = np.unique(np.geomspace(min_window, max_window, num=max_lags).astype(int))
    sizes = sizes[sizes >= min_window]

    corrected_log_rs = []
    log_n = []
    for size in sizes:
        n_chunks = n // size
        if n_chunks < 1:
            continue
        rs_per_chunk = []
        for i in range(n_chunks):
            chunk = increments[i * size:(i + 1) * size]
            cum_dev = np.cumsum(chunk - chunk.mean())
            R = cum_dev.max() - cum_dev.min()
            S = chunk.std(ddof=0)
            if S > 0:
                rs_per_chunk.append(R / S)
        if not rs_per_chunk:
            continue
        rs_mean = float(np.mean(rs_per_chunk))
        e_rs = _expected_rs_iid(size)
        # Re-centered so that a true H=0.5 process regresses to slope 0.5,
        # not to whatever slope the biased raw R/S values would imply.
        corrected_log_rs.append(np.log(rs_mean) - np.log(e_rs) + 0.5 * np.log(size))
        log_n.append(np.log(size))

    if len(log_n) < 2:
        raise ValueError("not enough valid window sizes to fit a Hurst exponent")

    slope, _intercept = np.polyfit(log_n, corrected_log_rs, 1)
    return float(slope)


def half_life(series: np.ndarray) -> float:
    """Ornstein-Uhlenbeck mean-reversion half-life, in bars, via OLS.

    Fits  ds_t = -lambda * (s_{t-1} - mu) + eps_t  (dt=1 bar) by regressing
    the first difference on the lagged level:  ds_t = a + b*s_{t-1} + eps_t,
    where b = -lambda and a = lambda*mu. half_life = ln(2)/lambda.

    Returns +inf when b >= 0 (lambda <= 0): the fitted process is not
    mean-reverting at all (a random walk or trending), so "time to revert
    halfway" has no finite answer -- inf is the honest value, not a
    sentinel to special-case away, since it composes correctly with
    comparisons ("is half_life() < N bars" is False for any finite N,
    exactly as intended).
    """
    series = np.asarray(series, dtype=float)
    s_lag = series[:-1]
    ds = np.diff(series)
    slope, _intercept = np.polyfit(s_lag, ds, 1)
    lam = -slope
    if lam <= 0:
        return float("inf")
    return float(np.log(2) / lam)
