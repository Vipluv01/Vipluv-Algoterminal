"""Tests for the stylized-facts analysis itself, using synthetic series with
KNOWN properties -- the same discipline as bench/histogram_test.go in the Go
side: validate the measurement tool against ground truth before trusting
what it reports about the simulation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

import numpy as np
import pytest

from stylized_facts import analyze, log_returns, resample_to_grid


def test_pure_gbm_shows_none_of_the_stylized_facts():
    """The negative control. Pure GBM returns are i.i.d. Gaussian by
    construction -- no fat tails, no autocorrelation, no vol clustering.
    This is exactly the failure mode of the original flawed design (GBM
    setting the traded price directly), and it's what proves the analysis
    module can tell the difference rather than rubber-stamping everything.
    """
    rng = np.random.default_rng(1)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 5000)))
    report = analyze(prices)

    assert not report.has_fat_tails, f"pure GBM should not show fat tails, got kurtosis={report.excess_kurtosis:.2f}"
    assert report.returns_are_weakly_autocorrelated
    assert not report.has_volatility_clustering, \
        f"pure GBM should show no vol clustering, got autocorr={report.abs_return_autocorr_lag1:.3f}"


def test_student_t_returns_show_fat_tails():
    """Positive control: a distribution known to have fat tails (Student's
    t, low degrees of freedom) must be DETECTED as such."""
    rng = np.random.default_rng(2)
    rets = rng.standard_t(df=3, size=5000) * 0.001
    prices = 100 * np.exp(np.cumsum(rets))
    report = analyze(prices)
    assert report.has_fat_tails, f"Student-t(df=3) returns should show fat tails, got kurtosis={report.excess_kurtosis:.2f}"


def test_garch_like_series_shows_volatility_clustering():
    """Positive control: a simple GARCH(1,1)-like process, constructed so
    volatility literally clusters by design, must be DETECTED as such."""
    rng = np.random.default_rng(3)
    n = 5000
    vol = np.zeros(n)
    vol[0] = 0.001
    rets = np.zeros(n)
    for i in range(1, n):
        vol[i] = np.sqrt(1e-7 + 0.15 * rets[i - 1] ** 2 + 0.80 * vol[i - 1] ** 2)
        rets[i] = vol[i] * rng.normal()
    prices = 100 * np.exp(np.cumsum(rets))
    report = analyze(prices)
    assert report.has_volatility_clustering, \
        f"GARCH-like series should show vol clustering, got autocorr={report.abs_return_autocorr_lag1:.3f} (p={report.abs_return_autocorr_pvalue:.4f})"


def test_resample_to_grid_forward_fills_gaps():
    prices = np.array([100.0, 101.0, 99.0])
    times = np.array([0, 0, 3])  # two trades at step 0, nothing until step 3
    grid = resample_to_grid(prices, times, n_steps=5)
    assert grid[0] == 101.0, "last trade within a step should win"
    assert grid[1] == 101.0 and grid[2] == 101.0, "no-trade steps must forward-fill"
    assert grid[3] == 99.0
    assert grid[4] == 99.0


def test_log_returns_basic():
    rets = log_returns(np.array([100.0, 110.0, 99.0]))
    assert rets[0] == pytest.approx(np.log(1.10))
    assert rets[1] == pytest.approx(np.log(99.0 / 110.0))
