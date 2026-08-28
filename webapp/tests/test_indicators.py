"""Validates each indicator against a case with a known-by-construction
answer, before anything trades on top of it."""

import numpy as np
import pytest

from app.strategies.indicators import atr, bollinger_bandwidth, bollinger_bands, ema, macd, rsi, sma, vwap


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


# ---------------------------------------------------------------------------
# sma
# ---------------------------------------------------------------------------

def test_sma_of_a_constant_series_equals_the_constant():
    values = np.full(30, 42.0)
    assert np.allclose(sma(values, period=10)[9:], 42.0)


def test_sma_matches_hand_computed_average():
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    result = sma(values, period=3)
    # window [1,2,3] -> 2.0, [2,3,4] -> 3.0, [3,4,5] -> 4.0, [4,5,6] -> 5.0
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[4] == pytest.approx(4.0)
    assert result[5] == pytest.approx(5.0)


def test_sma_is_nan_before_the_period_fills():
    values = np.arange(5.0)
    result = sma(values, period=10)
    assert np.isnan(result).all()


def test_sma_lags_ema_reaction_to_a_step():
    """The classic distinguishing property: SMA weighs every point in the
    window equally, so it reacts to a step change more slowly than EMA,
    which weighs recent points more heavily."""
    values = np.concatenate([np.full(20, 100.0), np.full(20, 110.0)])
    sma_result = sma(values, period=10)
    ema_result = ema(values, span=10)
    idx = 22  # 3 steps after the jump at index 20
    assert abs(ema_result[idx] - 110.0) < abs(sma_result[idx] - 110.0)


# ---------------------------------------------------------------------------
# vwap
# ---------------------------------------------------------------------------

def test_vwap_of_a_constant_price_equals_that_price_regardless_of_volume():
    prices = np.full(10, 100.0)
    volumes = np.array([10, 20, 5, 0, 15, 30, 0, 0, 25, 10], dtype=float)
    result = vwap(prices, volumes)
    assert np.allclose(result, 100.0)


def test_vwap_matches_hand_computed_cumulative_average():
    prices = np.array([100.0, 102.0, 101.0, 103.0])
    volumes = np.array([10.0, 10.0, 10.0, 10.0])
    result = vwap(prices, volumes)
    expected = np.cumsum(prices * volumes) / np.cumsum(volumes)
    assert np.allclose(result, expected)


def test_vwap_weights_higher_volume_bars_more_heavily():
    """Two prices, one traded at 10x the volume of the other -- the VWAP
    must land much closer to the high-volume price than a plain average
    would."""
    prices = np.array([100.0, 110.0])
    volumes = np.array([100.0, 10.0])  # heavily weighted toward the FIRST price
    result = vwap(prices, volumes)
    plain_average = prices.mean()
    assert abs(result[-1] - 100.0) < abs(plain_average - 100.0)


def test_vwap_holds_its_last_value_through_a_zero_volume_bar():
    prices = np.array([100.0, 105.0, 999.0])  # the 999 at zero volume must not move VWAP
    volumes = np.array([10.0, 10.0, 0.0])
    result = vwap(prices, volumes)
    assert result[-1] == pytest.approx(result[-2])


def test_vwap_is_nan_before_any_volume_has_traded():
    prices = np.array([100.0, 100.0, 100.0])
    volumes = np.array([0.0, 0.0, 10.0])
    result = vwap(prices, volumes)
    assert np.isnan(result[0])
    assert np.isnan(result[1])
    assert not np.isnan(result[2])


def test_vwap_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        vwap(np.array([1.0, 2.0]), np.array([1.0]))


# ---------------------------------------------------------------------------
# atr
# ---------------------------------------------------------------------------

def test_atr_of_a_perfectly_flat_series_is_zero():
    """No range, no gaps -- true range is 0 at every step, so ATR must be
    exactly 0."""
    n = 30
    high = np.full(n, 100.0)
    low = np.full(n, 100.0)
    close = np.full(n, 100.0)
    result = atr(high, low, close, period=14)
    assert result[14] == pytest.approx(0.0)
    assert result[-1] == pytest.approx(0.0)


def test_atr_matches_hand_computed_value_for_a_constant_range():
    """Every bar has the same high-low range (2.0) and no gaps from the
    previous close -- true range is exactly 2.0 every step, so ATR must
    converge to exactly 2.0."""
    n = 30
    close = np.full(n, 100.0)
    high = close + 1.0
    low = close - 1.0
    result = atr(high, low, close, period=14)
    assert result[14] == pytest.approx(2.0)
    assert result[-1] == pytest.approx(2.0)


def test_atr_captures_a_gap_through_the_previous_close():
    """A gap up with a NARROW high-low range that day must still register
    a large true range, via |high - prev_close| -- this is what
    distinguishes ATR from a plain high-low range measure."""
    high = np.array([100.0, 130.0])  # gaps from 100 up to ~130
    low = np.array([99.0, 129.0])    # that day's own range is only 1.0
    close = np.array([100.0, 129.5])
    # True range at index 1: max(130-129, |130-100|, |129-100|) = max(1, 30, 29) = 30
    n = 20
    high_series = np.concatenate([high, np.full(n - 2, 129.5 + 0.5)])
    low_series = np.concatenate([low, np.full(n - 2, 129.5 - 0.5)])
    close_series = np.concatenate([close, np.full(n - 2, 129.5)])
    result = atr(high_series, low_series, close_series, period=14)
    # The gap bar's true range (30) must have pulled the running ATR well
    # above what a flat ~1.0-range day alone would produce.
    assert result[14] > 1.0


def test_atr_is_nan_before_enough_data_exists():
    n = 10
    high = np.full(n, 101.0)
    low = np.full(n, 99.0)
    close = np.full(n, 100.0)
    result = atr(high, low, close, period=14)
    assert np.isnan(result).all()


def test_atr_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        atr(np.array([1.0, 2.0]), np.array([1.0]), np.array([1.0, 2.0]))


# ---------------------------------------------------------------------------
# bollinger_bandwidth
# ---------------------------------------------------------------------------

def test_bollinger_bandwidth_is_zero_for_a_flat_series():
    """No variance -> upper == lower == mid -> bandwidth exactly 0."""
    values = np.full(30, 100.0)
    result = bollinger_bandwidth(values, period=20)
    assert result[19] == pytest.approx(0.0)


def test_bollinger_bandwidth_matches_hand_computed_value():
    """Checks bandwidth's ARITHMETIC (is it really (upper-lower)/mid)
    against the library's own bollinger_bands output directly -- not
    re-deriving bollinger_bands' own correctness, which
    test_bollinger_bands_* above already covers."""
    rng = np.random.default_rng(0)
    values = 100 + rng.normal(0, 2.0, 40)
    lower, mid, upper = bollinger_bands(values, window=20)
    result = bollinger_bandwidth(values, period=20)
    valid = ~np.isnan(mid)
    expected = (upper[valid] - lower[valid]) / mid[valid]
    assert np.allclose(result[valid], expected)


def test_bollinger_bandwidth_rises_with_increasing_volatility():
    """A squeeze/expansion measure: a series with visibly wider swings in
    its recent window must show higher bandwidth than a calmer one over
    the same window size."""
    rng = np.random.default_rng(0)
    calm = 100 + rng.normal(0, 0.2, 40)
    volatile = 100 + rng.normal(0, 5.0, 40)
    bw_calm = bollinger_bandwidth(calm, period=20)
    bw_volatile = bollinger_bandwidth(volatile, period=20)
    assert bw_volatile[-1] > bw_calm[-1]


def test_bollinger_bandwidth_is_nan_before_the_window_fills():
    values = np.arange(10.0)
    result = bollinger_bandwidth(values, period=20)
    assert np.isnan(result).all()
