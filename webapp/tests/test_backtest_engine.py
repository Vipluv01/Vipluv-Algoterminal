import numpy as np
import pytest

from app.backtest.adapters import BACKTEST_BARS_PER_YEAR, BacktestOrder, BacktestStrategy
from app.backtest.engine import (
    BARS_PER_YEAR,
    _build_orders,
    _calmar_ratio,
    _mark_to_market_equity_curve,
    _max_drawdown,
    _sharpe_ratio,
    run_backtest,
)
from app.backtest.paths import AssetPath, MultiAssetHistory


def test_options_and_equity_backtest_domains_share_exactly_one_clock():
    """app.backtest.adapters.BACKTEST_BARS_PER_YEAR can't import
    app.backtest.engine.BARS_PER_YEAR directly (engine.py already imports
    FROM adapters.py, so the reverse would be circular) -- it's defined
    independently with the identical value instead. This is the guard that
    catches the two silently drifting apart, which is exactly the class of
    bug Phase 5.5 already found once (options pricing using a fixed vol
    disconnected from the underlying's own realized vol) and this whole
    A1/A2 pass found a second time (Sharpe annualizing as if a bar were a
    day when it's a second) -- two clocks for the same bar sequence, again,
    would be a third instance of the identical mistake."""
    assert BACKTEST_BARS_PER_YEAR == BARS_PER_YEAR


def _flat_history(steps: int, symbol: str = "X", price: float = 100.0) -> MultiAssetHistory:
    """A single-symbol, perfectly flat price path -- for tests that need
    full control over fill price rather than the real simulated path."""
    arr = np.full(steps, price)
    path = AssetPath(symbol=symbol, open=arr, high=arr, low=arr, close=arr, volume=np.zeros(steps))
    return MultiAssetHistory(steps=steps, seed=0, symbols=(symbol,), paths={symbol: path})


def _stepped_history(steps: int, symbol: str, prices: np.ndarray) -> MultiAssetHistory:
    path = AssetPath(symbol=symbol, open=prices, high=prices, low=prices, close=prices, volume=np.zeros(steps))
    return MultiAssetHistory(steps=steps, seed=0, symbols=(symbol,), paths={symbol: path})


class _NeverTradesStrategy:
    key = "never_trades"

    def evaluate(self, history, step):
        return []

    def reset(self):
        pass


class _FixedScheduleStrategy:
    """Trades on an exact, hand-specified schedule -- {step: [(symbol, side, qty), ...]} --
    so a test can assert on EXACTLY what the engine did with a known set of orders,
    rather than depend on any real strategy's threshold logic."""

    key = "fixed_schedule"

    def __init__(self, schedule: dict[int, list[tuple[str, str, int]]]):
        self.key = "fixed_schedule"
        self._schedule = schedule

    def evaluate(self, history, step):
        return [BacktestOrder(sym, side, qty) for sym, side, qty in self._schedule.get(step, [])]

    def reset(self):
        pass


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

def test_fee_bps_worsens_the_effective_fill_price():
    history = _flat_history(5, price=100.0)
    strategy = _FixedScheduleStrategy({0: [("X", "buy", 10)]})
    orders, _steps = _build_orders(strategy, history, fee_bps=100.0)  # 1% fee, exaggerated for a clear signal
    assert orders[0].px == pytest.approx(101.0)  # buy costs MORE with a fee


def test_fee_bps_worsens_a_sell_fill_price_in_the_other_direction():
    history = _flat_history(5, price=100.0)
    strategy = _FixedScheduleStrategy({0: [("X", "sell", 10)]})
    orders, _steps = _build_orders(strategy, history, fee_bps=100.0)
    assert orders[0].px == pytest.approx(99.0)  # sell fetches LESS with a fee


def test_zero_fee_bps_fills_at_the_raw_close():
    history = _flat_history(5, price=100.0)
    strategy = _FixedScheduleStrategy({2: [("X", "buy", 10)]})
    orders, steps = _build_orders(strategy, history, fee_bps=0.0)
    assert orders[0].px == pytest.approx(100.0)
    assert steps == [2]


# ---------------------------------------------------------------------------
# Equity curve / accounting reuse
# ---------------------------------------------------------------------------

def test_equity_curve_is_flat_at_initial_cash_with_no_trades():
    history = _flat_history(20)
    result = run_backtest(_NeverTradesStrategy(), history, initial_cash=100_000.0)
    assert np.all(result.equity_curve == 100_000.0)
    assert result.round_trips_closed == 0
    assert result.win_rate is None
    assert result.profit_factor is None


def test_equity_steps_down_on_a_buy_fill_by_cost_plus_fee():
    history = _flat_history(10, price=100.0)
    strategy = _FixedScheduleStrategy({3: [("X", "buy", 10)]})
    result = run_backtest(strategy, history, initial_cash=100_000.0, fee_bps=2.0)
    fee_price = 100.0 * 1.0002
    expected_cash_after = 100_000.0 - 10 * fee_price
    # At a flat price, mark-to-market equity == cash - cost + qty*price ==
    # cash_after_fee_paid + qty*mark; since mark == raw price (not the fee
    # price), the position is worth slightly LESS than what was paid for
    # it -- the fee is a real, realized drag on equity from bar 3 onward.
    expected_equity = expected_cash_after + 10 * 100.0
    assert result.equity_curve[2] == pytest.approx(100_000.0)  # untouched before the fill
    assert result.equity_curve[3] == pytest.approx(expected_equity)
    assert result.equity_curve[-1] == pytest.approx(expected_equity)  # holds the position to the end


def test_a_full_round_trip_realizes_exactly_the_price_move_minus_fees():
    prices = np.array([100.0] * 5 + [110.0] * 5)  # jumps up after entry
    history = _stepped_history(10, "X", prices)
    strategy = _FixedScheduleStrategy({1: [("X", "buy", 10)], 6: [("X", "sell", 10)]})
    result = run_backtest(strategy, history, initial_cash=100_000.0, fee_bps=0.0)

    # Bought 10 @ 100, sold 10 @ 110, zero fees -> realized +100 exactly.
    assert result.final_equity == pytest.approx(100_100.0)
    assert result.round_trips_closed == 1
    assert result.win_rate == pytest.approx(1.0)


def test_uses_the_real_accounting_module_not_a_reimplementation():
    """Direct proof of the phase's core requirement: constructing the same
    orders and running them through app.accounting._walk_fills by hand
    must match what run_backtest's own equity curve computed from them."""
    from app.accounting import _walk_fills

    prices = np.array([100.0] * 5 + [103.0] * 5)
    history = _stepped_history(10, "X", prices)
    strategy = _FixedScheduleStrategy({2: [("X", "buy", 7)]})
    orders, _steps = _build_orders(strategy, history, fee_bps=0.0)

    walked = _walk_fills(orders, 100_000.0)
    expected_final_equity = walked.cash + walked.qty["X"] * 103.0  # marked at the last close

    result = run_backtest(strategy, history, initial_cash=100_000.0, fee_bps=0.0)
    assert result.final_equity == pytest.approx(expected_final_equity)


# ---------------------------------------------------------------------------
# Sharpe / drawdown / calmar -- known-by-construction cases
# ---------------------------------------------------------------------------

def test_sharpe_is_none_for_a_flat_equity_curve():
    """A flat curve has zero return variance -- there is no valid Sharpe
    to report, not a fabricated 0.0 (which would read as "measured, and
    it's flat" rather than "the measurement is invalid")."""
    curve = np.full(100, 100_000.0)
    sharpe, reason = _sharpe_ratio(curve)
    assert sharpe is None
    assert reason is not None


def test_sharpe_is_positive_for_a_steadily_rising_curve_with_realistic_noise():
    """A PURE deterministic drift (no noise at all) hits the
    MAX_PLAUSIBLE_ANNUALIZED_RATIO guard -- see
    test_sharpe_is_none_for_an_implausibly_smooth_drift below -- so this
    adds small realistic bar-to-bar noise on top of the drift, the same
    way a real (if smooth) strategy's returns would never be perfectly
    constant."""
    # noise std well above the per-bar drift -- a raw (pre-annualization)
    # mean/std ratio near ~0.002, comfortably under the guard's ~0.0041 raw
    # threshold (10.0 annualized / sqrt(BARS_PER_YEAR) -- a bar is one
    # simulated SECOND, see engine.py's own BARS_PER_YEAR comment, so this
    # threshold is a much smaller RAW ratio than the old, wrong sqrt(252)
    # convention implied).
    rng = np.random.default_rng(0)
    drift = 1.000001 ** np.arange(300)
    noise = 1.0 + rng.normal(0, 0.0005, size=300)
    curve = 100_000.0 * drift * noise
    sharpe, reason = _sharpe_ratio(curve)
    assert reason is None
    assert sharpe > 0


def test_sharpe_is_negative_for_a_steadily_falling_curve_with_realistic_noise():
    rng = np.random.default_rng(1)
    drift = 0.999999 ** np.arange(300)
    noise = 1.0 + rng.normal(0, 0.0005, size=300)
    curve = 100_000.0 * drift * noise
    sharpe, reason = _sharpe_ratio(curve)
    assert reason is None
    assert sharpe < 0


def test_sharpe_is_none_for_an_implausibly_smooth_drift():
    """The exact bug this guard exists to catch: a near-perfectly-smooth,
    nonzero drift (essentially zero return variance around a consistent
    mean) produces a raw mean/std ratio no real strategy could have --
    found directly via delta_neutral's theta-decay-dominated backtest
    P&L, which produced an annualized Sharpe of +2667 before this guard
    existed."""
    curve = 100_000.0 * (1.00001 ** np.arange(300))  # no noise at all
    sharpe, reason = _sharpe_ratio(curve)
    assert sharpe is None
    assert "plausible bound" in reason


def test_max_drawdown_is_zero_for_a_monotonically_rising_curve():
    curve = np.linspace(100_000.0, 110_000.0, 50)
    assert _max_drawdown(curve) == pytest.approx(0.0)


def test_max_drawdown_matches_a_hand_computed_known_case():
    # Peak 100 -> trough 80 -> partial recovery to 95: max drawdown is
    # exactly (100-80)/100 = 0.20, not measured from the recovery point.
    curve = np.array([100.0, 90.0, 80.0, 85.0, 95.0])
    assert _max_drawdown(curve) == pytest.approx(0.20)


def test_max_drawdown_uses_the_running_peak_not_just_the_global_max():
    # A SECOND, smaller drawdown after a new peak must still be measured
    # from ITS OWN peak, not diluted by the earlier bigger one -- but the
    # reported max_drawdown is still the largest single drawdown overall.
    curve = np.array([100.0, 50.0, 100.0, 120.0, 90.0])
    # First drawdown: (100-50)/100 = 0.50. Second: (120-90)/120 = 0.25.
    assert _max_drawdown(curve) == pytest.approx(0.50)


def test_calmar_ratio_is_none_when_there_is_no_drawdown_at_all():
    """Undefined (division by zero), not a fabricated 0.0 -- "no drawdown
    observed" doesn't mean "zero risk-adjusted return," it means there's
    nothing valid to divide by."""
    curve = np.linspace(100_000.0, 105_000.0, 50)
    calmar, reason = _calmar_ratio(curve, max_drawdown=0.0)
    assert calmar is None
    assert reason is not None


def test_calmar_ratio_is_positive_for_positive_return_with_real_drawdown():
    # 50,000+ bars, not the ~220 an earlier version of this test used: a
    # bar is one simulated SECOND (see engine.py's own BARS_PER_YEAR
    # comment), and ANY non-trivial percentage move compressed into just
    # a few hundred seconds becomes an astronomical annualized figure once
    # multiplied by sqrt(BARS_PER_YEAR)'s ~2428x (or, for Calmar, a bare
    # BARS_PER_YEAR's ~5.9M x) scaling -- exactly the kind of number
    # MAX_PLAUSIBLE_ANNUALIZED_RATIO exists to reject. A realistic
    # 2%-drawdown-then-2.1%-recovery pattern needs to play out over tens
    # of thousands of bars (hours of simulated time) to annualize to a
    # plausible, real-strategy-shaped Calmar instead.
    curve = np.concatenate([np.linspace(100_000, 98_000, 2000), np.linspace(98_000, 98_000 * 1.021, 50_000)])
    dd = _max_drawdown(curve)
    assert dd > 0
    calmar, reason = _calmar_ratio(curve, dd)
    assert reason is None
    assert calmar > 0


# ---------------------------------------------------------------------------
# End-to-end against the real simulated paths (each adapter shape)
# ---------------------------------------------------------------------------

def test_run_backtest_end_to_end_single_instrument():
    from app.backtest.adapters import SingleInstrumentAdapter
    from app.backtest.paths import get_market_paths
    from app.strategies.alpha import AlphaRSIEMAStrategy

    path = get_market_paths(steps=400, seed=100)
    adapter = SingleInstrumentAdapter(AlphaRSIEMAStrategy(), "ICICIBANK")
    result = run_backtest(adapter, path)

    assert result.strategy_key == "alpha_rsi_ema"
    assert result.steps == 400
    assert len(result.equity_curve) == 400
    assert result.initial_cash == 100_000.0
    assert result.sharpe_ratio is None or np.isfinite(result.sharpe_ratio)
    assert 0.0 <= result.max_drawdown <= 1.0


def test_run_backtest_end_to_end_pairs():
    from app.backtest.adapters import PairsAdapter
    from app.backtest.paths import get_market_paths
    from app.strategies.pairs_cointegration import PairsCointegrationStrategy

    path = get_market_paths(steps=500, seed=101)
    adapter = PairsAdapter(PairsCointegrationStrategy(), "ICICIBANK", "HDFCBANK")
    result = run_backtest(adapter, path)

    assert result.strategy_key == "pairs_cointegration"
    assert len(result.equity_curve) == 500
    assert result.sharpe_ratio is None or np.isfinite(result.sharpe_ratio)


def test_run_backtest_end_to_end_basket():
    from app.backtest.adapters import BasketAdapter
    from app.backtest.paths import get_market_paths
    from app.strategies.multi_basket import MultiBasketStrategy

    path = get_market_paths(steps=800, seed=42)  # seed known (from manual exploration) to produce real trades
    adapter = BasketAdapter(MultiBasketStrategy(), ("ICICIBANK", "HDFCBANK", "SBIN"))
    result = run_backtest(adapter, path)

    assert result.strategy_key == "multi_basket"
    assert result.round_trips_closed > 0
    assert result.sharpe_ratio is None or np.isfinite(result.sharpe_ratio)


def test_backtest_is_deterministic_given_the_same_path():
    from app.backtest.adapters import SingleInstrumentAdapter
    from app.backtest.paths import generate_market_paths
    from app.strategies.momentum import MomentumMACDStrategy

    path = generate_market_paths(steps=300, seed=55)
    r1 = run_backtest(SingleInstrumentAdapter(MomentumMACDStrategy(), "ICICIBANK"), path)
    r2 = run_backtest(SingleInstrumentAdapter(MomentumMACDStrategy(), "ICICIBANK"), path)
    assert r1.sharpe_ratio == r2.sharpe_ratio
    assert r1.round_trips_closed == r2.round_trips_closed
    assert np.array_equal(r1.equity_curve, r2.equity_curve)
