import numpy as np
import pytest

from app.strategies.alpha import AlphaRSIEMAStrategy
from app.strategies.base import MarketSnapshot
from app.strategies.mean_reversion_bb import MeanReversionBollingerStrategy
from app.strategies.momentum import MomentumMACDStrategy


def test_alpha_returns_none_with_insufficient_history():
    strat = AlphaRSIEMAStrategy()
    result = strat.evaluate(MarketSnapshot("X", np.array([100.0, 101.0])))
    assert result is None


def test_alpha_fires_buy_on_a_recovery_from_a_dip():
    # A dip (bearish) followed by a sustained rally forces a bullish
    # EMA9/EMA21 crossover with RSI climbing out of oversold territory.
    dip = np.linspace(110, 90, 15)
    rally = np.linspace(90, 130, 30)
    prices = np.concatenate([dip, rally])
    strat = AlphaRSIEMAStrategy()
    signals = [strat.evaluate(MarketSnapshot("X", prices[: i + 1])) for i in range(len(prices))]
    fired = [s for s in signals if s is not None]
    assert any(s.side == "buy" for s in fired), "a sustained recovery from a dip must eventually fire a buy"


def test_alpha_fires_sell_on_a_reversal_from_a_rally():
    rally = np.linspace(90, 130, 15)
    drop = np.linspace(130, 90, 30)
    prices = np.concatenate([rally, drop])
    strat = AlphaRSIEMAStrategy()
    signals = [strat.evaluate(MarketSnapshot("X", prices[: i + 1])) for i in range(len(prices))]
    fired = [s for s in signals if s is not None]
    assert any(s.side == "sell" for s in fired), "a sustained reversal from a rally must eventually fire a sell"


def test_momentum_returns_none_with_insufficient_history():
    strat = MomentumMACDStrategy()
    result = strat.evaluate(MarketSnapshot("X", np.linspace(100, 110, 40)))
    assert result is None


def test_momentum_fires_buy_during_a_sustained_uptrend_after_a_brief_dip():
    # Flat, then a shallow dip (drags MACD histogram briefly negative
    # without breaking the EMA50 trend), then resumes climbing -- the
    # histogram crossing back to positive while still above EMA50 is
    # exactly the "momentum with the trend" case this strategy targets.
    base = np.full(60, 100.0)
    dip = np.linspace(100, 97, 8)
    resume = np.linspace(97, 130, 40)
    prices = np.concatenate([base, dip, resume])
    strat = MomentumMACDStrategy()
    signals = [strat.evaluate(MarketSnapshot("X", prices[: i + 1])) for i in range(len(prices))]
    fired = [s for s in signals if s is not None]
    assert any(s.side == "buy" for s in fired)


def test_mean_reversion_bb_fires_buy_when_price_dips_below_the_lower_band():
    stable = np.full(30, 100.0)
    spike_down = np.array([100.0, 95.0, 90.0, 85.0])  # sharp drop -- well outside a tight recent range
    prices = np.concatenate([stable, spike_down])
    strat = MeanReversionBollingerStrategy()
    signals = [strat.evaluate(MarketSnapshot("X", prices[: i + 1])) for i in range(len(prices))]
    fired = [s for s in signals if s is not None]
    assert any(s.side == "buy" for s in fired)


def test_mean_reversion_bb_fires_sell_when_price_spikes_above_the_upper_band():
    stable = np.full(30, 100.0)
    spike_up = np.array([100.0, 105.0, 110.0, 115.0])
    prices = np.concatenate([stable, spike_up])
    strat = MeanReversionBollingerStrategy()
    signals = [strat.evaluate(MarketSnapshot("X", prices[: i + 1])) for i in range(len(prices))]
    fired = [s for s in signals if s is not None]
    assert any(s.side == "sell" for s in fired)


def test_mean_reversion_bb_returns_none_with_insufficient_history():
    strat = MeanReversionBollingerStrategy()
    result = strat.evaluate(MarketSnapshot("X", np.array([100.0, 101.0, 99.0])))
    assert result is None
