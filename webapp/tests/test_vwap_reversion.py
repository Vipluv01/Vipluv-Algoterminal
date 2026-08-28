import numpy as np
import pytest

from app.strategies.base import MarketSnapshot
from app.strategies.vwap_reversion import VWAPReversionStrategy


def test_returns_none_with_no_volume_data():
    """VWAP is meaningless without volume -- must degrade to "no signal,"
    not crash or divide by zero."""
    strat = VWAPReversionStrategy()
    prices = 100 + np.random.default_rng(0).normal(0, 0.2, 40)
    result = strat.evaluate(MarketSnapshot("X", prices, volumes=None))
    assert result is None


def test_returns_none_with_insufficient_history():
    strat = VWAPReversionStrategy(window=30)
    prices = np.array([100.0, 100.1, 99.9])
    volumes = np.array([10.0, 10.0, 10.0])
    result = strat.evaluate(MarketSnapshot("X", prices, volumes))
    assert result is None


def test_fires_sell_when_price_spikes_far_above_vwap():
    rng = np.random.default_rng(0)
    n = 60
    prices = np.concatenate([100 + rng.normal(0, 0.2, n - 1), [115.0]])  # sudden spike
    volumes = np.full(n, 100.0)
    strat = VWAPReversionStrategy()
    result = strat.evaluate(MarketSnapshot("X", prices, volumes))
    assert result is not None
    assert result.side == "sell"


def test_fires_buy_when_price_drops_far_below_vwap():
    rng = np.random.default_rng(0)
    n = 60
    prices = np.concatenate([100 + rng.normal(0, 0.2, n - 1), [85.0]])  # sudden drop
    volumes = np.full(n, 100.0)
    strat = VWAPReversionStrategy()
    result = strat.evaluate(MarketSnapshot("X", prices, volumes))
    assert result is not None
    assert result.side == "buy"


def test_no_signal_when_price_tracks_vwap_closely():
    rng = np.random.default_rng(0)
    prices = 100 + rng.normal(0, 0.2, 60)
    volumes = np.full(60, 100.0)
    strat = VWAPReversionStrategy()
    result = strat.evaluate(MarketSnapshot("X", prices, volumes))
    assert result is None


def test_a_small_price_move_on_heavy_volume_deviates_less_than_on_thin_volume():
    """The defining property that distinguishes VWAP from a plain moving
    average: the SAME final price move produces a smaller deviation from
    VWAP when it happens on heavy volume (VWAP itself shifts toward the
    new price) than on thin volume (VWAP barely moves)."""
    rng = np.random.default_rng(1)
    base = 100 + rng.normal(0, 0.1, 59)
    prices = np.concatenate([base, [103.0]])

    thin_volumes = np.concatenate([np.full(59, 100.0), [1.0]])   # final bar is thin
    heavy_volumes = np.concatenate([np.full(59, 100.0), [5000.0]])  # final bar is heavy

    from app.strategies.indicators import vwap
    thin_vwap = vwap(prices, thin_volumes)
    heavy_vwap = vwap(prices, heavy_volumes)

    thin_deviation = abs(prices[-1] - thin_vwap[-1])
    heavy_deviation = abs(prices[-1] - heavy_vwap[-1])
    assert heavy_deviation < thin_deviation


def test_qty_matches_configured_default():
    rng = np.random.default_rng(0)
    n = 60
    prices = np.concatenate([100 + rng.normal(0, 0.2, n - 1), [115.0]])
    volumes = np.full(n, 100.0)
    strat = VWAPReversionStrategy(qty=25)
    result = strat.evaluate(MarketSnapshot("X", prices, volumes))
    assert result.qty == 25
