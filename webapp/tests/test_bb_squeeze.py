import numpy as np
import pytest

from app.strategies.base import MarketSnapshot
from app.strategies.bb_squeeze import BBSqueezeStrategy


def test_returns_none_with_insufficient_history():
    strat = BBSqueezeStrategy()
    prices = np.full(50, 100.0)  # well short of squeeze_lookback + period
    result = strat.evaluate(MarketSnapshot("X", prices))
    assert result is None


def test_no_signal_during_uniform_low_volatility_with_no_relative_compression():
    """A constant-volatility series has no SQUEEZE (nothing dipped below
    its own recent range) even though it's quiet overall -- squeeze
    detection is relative to the series' own recent bandwidth history,
    not an absolute threshold."""
    rng = np.random.default_rng(0)
    strat = BBSqueezeStrategy()
    prices = 100 + rng.normal(0, 0.05, 145)
    result = strat.evaluate(MarketSnapshot("X", prices))
    assert result is None


def test_fires_buy_on_a_genuine_squeeze_followed_by_an_upward_breakout():
    rng = np.random.default_rng(0)
    baseline = 100 + np.cumsum(rng.normal(0, 0.3, 110))
    squeeze = baseline[-1] + np.cumsum(rng.normal(0, 0.03, 30))  # genuinely tighter than baseline
    breakout = np.concatenate([baseline, squeeze, [squeeze[-1] + 3.0]])

    strat = BBSqueezeStrategy()
    result = strat.evaluate(MarketSnapshot("X", breakout))
    assert result is not None
    assert result.side == "buy"


def test_fires_sell_on_a_genuine_squeeze_followed_by_a_downward_breakout():
    rng = np.random.default_rng(0)
    baseline = 100 + np.cumsum(rng.normal(0, 0.3, 110))
    squeeze = baseline[-1] + np.cumsum(rng.normal(0, 0.03, 30))
    breakout = np.concatenate([baseline, squeeze, [squeeze[-1] - 3.0]])

    strat = BBSqueezeStrategy()
    result = strat.evaluate(MarketSnapshot("X", breakout))
    assert result is not None
    assert result.side == "sell"


def test_no_signal_when_squeezed_but_not_yet_broken_out():
    rng = np.random.default_rng(0)
    baseline = 100 + np.cumsum(rng.normal(0, 0.3, 110))
    squeeze = baseline[-1] + np.cumsum(rng.normal(0, 0.03, 31))  # still inside the squeeze, no breakout bar
    prices = np.concatenate([baseline, squeeze])

    strat = BBSqueezeStrategy()
    result = strat.evaluate(MarketSnapshot("X", prices))
    assert result is None


def test_no_signal_on_a_breakout_that_was_never_preceded_by_a_squeeze():
    """A sharp move after ordinary (not compressed) volatility is NOT what
    this strategy trades -- distinguishes it from a naive "big move -> buy"
    rule, and from mean_reversion_bb.py's opposite (fade) behavior."""
    rng = np.random.default_rng(0)
    noisy = 100 + rng.normal(0, 2.0, 145)  # ordinary volatility throughout, no compression
    breakout = np.concatenate([noisy, [noisy[-1] + 5.0]])

    strat = BBSqueezeStrategy()
    result = strat.evaluate(MarketSnapshot("X", breakout))
    assert result is None


def test_qty_matches_configured_default():
    rng = np.random.default_rng(0)
    baseline = 100 + np.cumsum(rng.normal(0, 0.3, 110))
    squeeze = baseline[-1] + np.cumsum(rng.normal(0, 0.03, 30))
    breakout = np.concatenate([baseline, squeeze, [squeeze[-1] + 3.0]])

    strat = BBSqueezeStrategy(qty=42)
    result = strat.evaluate(MarketSnapshot("X", breakout))
    assert result.qty == 42
