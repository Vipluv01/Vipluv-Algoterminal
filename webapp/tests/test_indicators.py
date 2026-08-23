"""Validates each indicator against a case with a known-by-construction
answer, before anything trades on top of it."""

import numpy as np
import pytest

from app.strategies.indicators import bollinger_bands, ema, macd, rsi


def test_ema_of_a_constant_series_equals_the_constant():
    values = np.full(30, 42.0)
    result = ema(values, span=10)
    assert np.allclose(result, 42.0)


def test_ema_reacts_faster_with_a_shorter_span():
    # A step function: flat at 100, then jumps to 110 and stays.
    values = np.concatenate([np.full(20, 100.0), np.full(20, 110.0)])
    fast = ema(values, span=3)
    slow = ema(values, span=30)
    # 5 steps after the jump, the fast EMA must have closed more of the gap.
    idx = 25
    assert abs(fast[idx] - 110.0) < abs(slow[idx] - 110.0)


def test_rsi_is_100_when_every_delta_is_a_gain():
    values = np.arange(1.0, 30.0)  # strictly increasing -- no losses ever
    result = rsi(values, period=14)
    assert result[14] == pytest.approx(100.0)
    assert result[-1] == pytest.approx(100.0)


def test_rsi_is_0_when_every_delta_is_a_loss():
    values = np.arange(30.0, 1.0, -1.0)  # strictly decreasing -- no gains ever
    result = rsi(values, period=14)
    assert result[14] == pytest.approx(0.0)


def test_rsi_is_nan_before_enough_data_exists():
    values = np.arange(1.0, 10.0)  # only 8 deltas, period=14 needs 14
    result = rsi(values, period=14)
    assert np.isnan(result).all()


def test_macd_histogram_is_zero_for_a_flat_series():
    values = np.full(50, 50.0)
    _, _, hist = macd(values)
    assert np.allclose(hist, 0.0)


def test_macd_line_is_positive_during_a_sustained_uptrend():
    # Fast EMA (12) must pull above slow EMA (26) well into a steady climb.
    values = np.linspace(100, 200, 60)
    macd_line, _, _ = macd(values)
    assert macd_line[-1] > 0


def test_bollinger_bands_bracket_a_noisy_series_around_its_mean():
    rng = np.random.default_rng(0)
    values = 100 + rng.normal(0, 1, 100)
    lower, mid, upper = bollinger_bands(values, window=20, n_std=2.0)
    valid = ~np.isnan(mid)
    assert (upper[valid] > mid[valid]).all()
    assert (mid[valid] > lower[valid]).all()
    # For a stationary series, the sample mean of a 20-window band should
    # track close to the series' own true mean (100), not drift off.
    assert mid[valid].mean() == pytest.approx(100.0, abs=0.5)


def test_bollinger_bands_are_nan_before_the_window_fills():
    values = np.arange(10.0)
    lower, mid, upper = bollinger_bands(values, window=20)
    assert np.isnan(mid).all()
