"""OptionsBacktestAdapter -- pricing/mark_price correctness and end-to-end
run_backtest integration for the 4 multi-leg options strategies."""

from __future__ import annotations

import math

import pytest

from app.backtest.adapters import BACKTEST_BARS_PER_YEAR, OptionsBacktestAdapter, _bars_remaining_T
from app.backtest.engine import run_backtest
from app.backtest.paths import clear_path_cache, generate_market_paths
from app.strategies.calendar_spread import CalendarSpreadStrategy
from app.strategies.delta_neutral import DeltaNeutralStrategy
from app.strategies.iron_condor import IronCondorStrategy
from app.strategies.short_strangle import ShortStrangleStrategy


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_path_cache()
    yield
    clear_path_cache()


@pytest.fixture(scope="module")
def path():
    return generate_market_paths(steps=1200, seed=3)


# ---------------------------------------------------------------------------
# _bars_remaining_T
# ---------------------------------------------------------------------------

def test_bars_remaining_T_decays_linearly_toward_the_floor():
    T0 = _bars_remaining_T(expiry_bars=100, entry_step=0, step=0)
    T50 = _bars_remaining_T(expiry_bars=100, entry_step=0, step=50)
    T100 = _bars_remaining_T(expiry_bars=100, entry_step=0, step=100)
    assert T0 > T50 > T100
    assert T0 == pytest.approx(100 / BACKTEST_BARS_PER_YEAR)


def test_bars_remaining_T_never_reaches_zero_or_below():
    T = _bars_remaining_T(expiry_bars=10, entry_step=0, step=10_000)
    assert T > 0.0


# ---------------------------------------------------------------------------
# mark_price -- stateless, symbol-decoded pricing
# ---------------------------------------------------------------------------

def test_mark_price_returns_none_for_a_plain_equity_symbol(path):
    adapter = OptionsBacktestAdapter(DeltaNeutralStrategy())
    assert adapter.mark_price("ICICIBANK", path, 5) is None


def test_mark_price_is_positive_and_finite_for_a_synthetic_contract_symbol(path):
    adapter = OptionsBacktestAdapter(ShortStrangleStrategy(underlying="BANKNIFTY"))
    symbol = "BANKNIFTY#0#200#1300CE"
    price = adapter.mark_price(symbol, path, 5)
    assert price is not None
    assert math.isfinite(price)
    assert price >= 0


def test_mark_price_is_stateless_across_a_position_the_adapter_has_since_moved_past(path):
    """The exact bug this design has to avoid: mark_price for a bar WHILE
    a position was open must not depend on the adapter's CURRENT (later,
    already-moved-on) self._open_legs -- see OptionsBacktestAdapter's own
    docstring."""
    adapter = OptionsBacktestAdapter(ShortStrangleStrategy(underlying="BANKNIFTY"))
    symbol = "BANKNIFTY#10#50#1300CE"
    price_before_mutation = adapter.mark_price(symbol, path, 30)
    # Mutate the adapter's internal state as if a LATER, unrelated cycle
    # were now open (a different entry_step/open_legs entirely) --
    # mark_price for THIS EARLIER symbol must be completely unaffected,
    # since every input it needs is decoded straight out of `symbol`.
    from app.strategies.options_base import OptionLegSignal
    adapter._entry_step = 999
    adapter._open_legs = (OptionLegSignal(option_type="CE", side="sell", strike=9999.0, qty=7, reason=""),)
    price_after_mutation = adapter.mark_price(symbol, path, 30)
    assert price_before_mutation == price_after_mutation


# ---------------------------------------------------------------------------
# End-to-end run_backtest for all 4 strategies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strategy_cls", [ShortStrangleStrategy, IronCondorStrategy, CalendarSpreadStrategy, DeltaNeutralStrategy])
def test_options_strategy_runs_end_to_end_without_error(path, strategy_cls):
    # A None sharpe_ratio is a legitimate outcome here -- run_backtest (unlike
    # run_monte_carlo) has no insufficient_horizon pre-check, and this
    # simulation's own realistic realized vol is low enough that a
    # near-flat or near-deterministic equity curve is a real, honest
    # possibility (see app.backtest.engine._sharpe_ratio's None contract).
    # What must NEVER happen is NaN/inf leaking through.
    adapter = OptionsBacktestAdapter(strategy_cls())
    result = run_backtest(adapter, path)
    assert result.sharpe_ratio is None or math.isfinite(result.sharpe_ratio)
    assert math.isfinite(result.max_drawdown)
    assert math.isfinite(result.final_equity)
    assert result.final_equity > 0  # never a negative/blown-up account from a pricing bug


@pytest.mark.parametrize("strategy_cls", [ShortStrangleStrategy, IronCondorStrategy, CalendarSpreadStrategy, DeltaNeutralStrategy])
def test_options_strategy_actually_trades_over_a_long_enough_path(path, strategy_cls):
    adapter = OptionsBacktestAdapter(strategy_cls())
    result = run_backtest(adapter, path)
    assert result.round_trips_closed > 0


def test_iron_condor_produces_a_multiple_of_four_legs_worth_of_realizations(path):
    """Every entry is 4 legs, every exit closes all 4 -- so total
    executed orders (not necessarily REALIZATIONS, which only count
    closing/reducing fills) should reflect that shape. Checked here via
    the adapter's own order construction directly."""
    strat = IronCondorStrategy(hold_bars=150)
    adapter = OptionsBacktestAdapter(strat)
    total_orders = 0
    for step in range(400):
        orders = adapter.evaluate(path, step)
        total_orders += len(orders)
    assert total_orders % 4 == 0
    assert total_orders > 0


def test_backtest_reset_clears_adapter_state_between_paths(path):
    strat = ShortStrangleStrategy(hold_bars=50)
    adapter = OptionsBacktestAdapter(strat)
    for step in range(100):
        adapter.evaluate(path, step)
    assert adapter._position in ("none", "open")  # sanity: ran without error

    adapter.reset()
    assert adapter._position == "none"
    assert adapter._open_legs == ()
    assert adapter._entry_step is None
    assert adapter._hedge_qty == 0


def test_delta_neutral_backtest_produces_both_option_and_equity_fills(path):
    strat = DeltaNeutralStrategy(hold_bars=600, rebalance_bars=100)
    adapter = OptionsBacktestAdapter(strat)
    all_orders = []
    for step in range(700):
        all_orders.extend(adapter.evaluate(path, step))
    option_orders = [o for o in all_orders if "#" in o.symbol]
    equity_orders = [o for o in all_orders if o.symbol == "ICICIBANK"]
    assert option_orders  # at least the entry leg
    assert equity_orders  # at least the initial hedge
