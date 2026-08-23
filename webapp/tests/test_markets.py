"""Verifies named, per-symbol markets are genuinely independent -- the
direct fix for a real complaint: an earlier version ran one anonymous
synthetic instrument with no name, when a serious multi-symbol terminal
needs clearly-identified tradable symbols."""

import numpy as np
import pytest

from app.markets import MarketRegistry, NAMED_INSTRUMENTS, SymbolMarket


def test_named_instruments_include_the_icici_hdfc_pair():
    """pairs_cointegration.py was validated against exactly this pair
    (icici_mean_reversion) -- it must actually be tradable here."""
    assert "ICICIBANK" in NAMED_INSTRUMENTS
    assert "HDFCBANK" in NAMED_INSTRUMENTS


def test_registry_creates_one_independent_market_per_symbol():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0, "HDFCBANK": 1650.0}, seed=0)
    try:
        assert set(registry.markets.keys()) == {"ICICIBANK", "HDFCBANK"}
        assert registry.markets["ICICIBANK"].s0 == 1250.0
        assert registry.markets["HDFCBANK"].s0 == 1650.0
    finally:
        registry.close()


def test_stepping_the_registry_advances_every_symbol_independently():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0, "HDFCBANK": 1650.0}, seed=0)
    try:
        for _ in range(20):
            prices = registry.step_all()
        assert set(prices.keys()) == {"ICICIBANK", "HDFCBANK"}
        icici_history = registry.prices("ICICIBANK")
        hdfc_history = registry.prices("HDFCBANK")
        assert len(icici_history) == 21  # seed price + 20 steps
        assert len(hdfc_history) == 21
        # Different starting prices and independent seeds/agent populations
        # -- the two series must not be identical.
        assert not np.array_equal(icici_history, hdfc_history)
    finally:
        registry.close()


def test_unknown_symbol_raises_a_clear_error():
    registry = MarketRegistry(symbols={"ICICIBANK": 1250.0}, seed=0)
    try:
        with pytest.raises(KeyError):
            registry.prices("NOTASYMBOL")
    finally:
        registry.close()


def test_symbol_market_price_history_starts_at_its_own_seed_price():
    m = SymbolMarket(symbol="RELIANCE", s0=2900.0, seed=1)
    try:
        assert m.price_history == [2900.0]
        m.step()
        assert len(m.price_history) == 2
    finally:
        m.close()
