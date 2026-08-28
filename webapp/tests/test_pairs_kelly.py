"""Same positive/negative-control discipline as test_pairs_cointegration.py,
plus the sizing behavior that's actually new here: Kelly-derived quantity
instead of a fixed one, and the exact-close-quantity correctness that
sizing variability puts at risk (see pairs_kelly.py's _close docstring)."""

import numpy as np
import pytest

from app.strategies.pairs_cointegration import PairSnapshot
from app.strategies.pairs_kelly import MIN_HISTORICAL_TRADES, PairsKellyStrategy, _scan_historical_trades


def _cointegrated_pair(n=300, seed=0, spread_amplitude=0.0):
    rng = np.random.default_rng(seed)
    b = 100 + np.cumsum(rng.normal(0, 0.5, n))
    noise = rng.normal(0, 0.3, n)
    a = b + 5.0 + noise
    if spread_amplitude:
        a = a.copy()
        a[-1] += spread_amplitude
    return a, b


def _independent_walks(n=300, seed=0):
    rng = np.random.default_rng(seed)
    a = 100 + np.cumsum(rng.normal(0, 1.0, n))
    b = 50 + np.cumsum(rng.normal(0, 1.0, n))
    return a, b


def test_genuinely_cointegrated_pair_can_enter():
    a, b = _cointegrated_pair(spread_amplitude=6.0)
    strat = PairsKellyStrategy(entry_z=1.5, zscore_window=60, min_history=90)
    pair = PairSnapshot("A", "B", a, b, position="none")
    result = strat.evaluate_pair(pair)
    assert result is not None
    assert result.new_position in ("long_spread", "short_spread")


def test_independent_walks_never_enter():
    a, b = _independent_walks()
    strat = PairsKellyStrategy(entry_z=1.5, zscore_window=60, min_history=90)
    pair = PairSnapshot("A", "B", a, b, position="none")
    result = strat.evaluate_pair(pair)
    assert result is None


def test_insufficient_history_returns_none():
    a, b = _cointegrated_pair(n=50)
    strat = PairsKellyStrategy(min_history=90)
    result = strat.evaluate_pair(PairSnapshot("A", "B", a, b, position="none"))
    assert result is None


# ---------------------------------------------------------------------------
# The historical-trade scan itself
# ---------------------------------------------------------------------------

def test_scan_finds_no_trades_in_a_flat_zscore_series():
    zscore = np.zeros(200)
    spread = np.zeros(200)
    result = _scan_historical_trades(zscore, spread, entry_z=1.5, exit_z=0.0, stop_z=3.0)
    assert result is None  # never crosses the entry threshold at all


def _build_trade(zscore: np.ndarray, spread: np.ndarray, *, start: int, period: int, win: bool) -> None:
    """Fills in one hypothetical long-spread trade starting at `start`:
    entry at z=-2.0 (below -entry_z=1.5), HOLDING there (not the array's
    default fill) for the whole period so it doesn't trivially "revert" on
    the very next bar, then resolving as a win (reverts through exit_z) or
    a loss (runs to stop_z) at `start + period`."""
    zscore[start:start + period] = -2.0
    spread[start:start + period] = 100.0
    if win:
        zscore[start + period] = 0.5    # reverts through exit_z=0.0
        spread[start + period] = 105.0  # spread ROSE -- profits a long-spread trade
    else:
        zscore[start + period] = -3.5   # hits stop_z=3.0 on the same side
        spread[start + period] = 95.0   # spread FELL -- a loss for a long-spread trade


def test_scan_counts_wins_and_losses_correctly_in_a_mixed_history():
    """Kelly needs BOTH a positive avg_win and avg_loss (position_sizing.
    kelly_fraction raises without both) -- an all-wins or all-losses
    history can't estimate that ratio at all (see the dedicated tests
    below for those degenerate cases). This is the ordinary case: a mix,
    with win_rate/avg_win/avg_loss all checked against hand-constructed
    values, not just "some result came back."""
    n = 300
    period = 20
    zscore = np.full(n, -2.0)
    spread = np.full(n, 100.0)
    outcomes = [True, True, False, True, False, False, True]  # 4 wins, 3 losses
    i = 0
    for win in outcomes:
        _build_trade(zscore, spread, start=i, period=period, win=win)
        i += period + 1

    result = _scan_historical_trades(zscore, spread, entry_z=1.5, exit_z=0.0, stop_z=3.0)
    assert result is not None
    assert result.n_trades == len(outcomes)
    assert result.win_rate == pytest.approx(4 / 7)
    assert result.avg_win == pytest.approx(5.0)   # every win resolves at spread 105 vs entry 100
    assert result.avg_loss == pytest.approx(5.0)  # every loss resolves at spread 95 vs entry 100


def test_scan_returns_none_for_an_all_wins_history():
    """No losses at all means no loss/win ratio to estimate -- Kelly's
    formula is undefined without one, so this must be treated as
    insufficient evidence, not "assume losses are free.\""""
    n = 200
    period = 15
    zscore = np.full(n, -2.0)
    spread = np.full(n, 100.0)
    i = 0
    while i + period < n:
        _build_trade(zscore, spread, start=i, period=period, win=True)
        i += period + 1

    result = _scan_historical_trades(zscore, spread, entry_z=1.5, exit_z=0.0, stop_z=3.0)
    assert result is None


def test_scan_returns_none_for_an_all_losses_history():
    n = 200
    period = 15
    zscore = np.full(n, -2.0)
    spread = np.full(n, 100.0)
    i = 0
    while i + period < n:
        _build_trade(zscore, spread, start=i, period=period, win=False)
        i += period + 1

    result = _scan_historical_trades(zscore, spread, entry_z=1.5, exit_z=0.0, stop_z=3.0)
    assert result is None


def test_scan_requires_a_minimum_number_of_resolved_trades():
    """Fewer than MIN_HISTORICAL_TRADES resolved hypothetical trades is
    treated as insufficient evidence, not a valid (if noisy) estimate --
    verified by constructing exactly one fewer than the minimum."""
    n = 200
    period = n // (MIN_HISTORICAL_TRADES - 1) // 2 - 1
    zscore = np.zeros(n)
    spread = np.zeros(n)
    i = 0
    count = 0
    while i + period < n and count < MIN_HISTORICAL_TRADES - 1:
        zscore[i] = -2.0
        spread[i] = 100.0
        zscore[i + period] = 0.5
        spread[i + period] = 105.0
        i += period + 1
        count += 1

    result = _scan_historical_trades(zscore, spread, entry_z=1.5, exit_z=0.0, stop_z=3.0)
    assert result is None


# ---------------------------------------------------------------------------
# Sizing and exact-close-quantity correctness
# ---------------------------------------------------------------------------

def test_falls_back_to_fallback_qty_with_no_historical_trade_evidence():
    """Right after min_history is first satisfied, there hasn't been time
    to accumulate MIN_HISTORICAL_TRADES resolved hypothetical trades --
    the strategy must still produce a usable qty (fallback_qty), not zero
    or a crash."""
    a, b = _cointegrated_pair(n=91, spread_amplitude=6.0)  # just past min_history=90
    strat = PairsKellyStrategy(entry_z=1.5, zscore_window=60, min_history=90, fallback_qty=17)
    result = strat.evaluate_pair(PairSnapshot("A", "B", a, b, position="none"))
    assert result is not None
    assert result.signal_a.qty == 17


def test_close_unwinds_exactly_what_position_qty_a_says_is_held():
    """The bug this test exists to catch: closing must use the ACTUAL
    held quantity (pair.position_qty_a), never a freshly-guessed one --
    otherwise a Kelly-sized entry could be left partially open. Force a
    close (via a stop-loss-triggering spread_amplitude) with a
    position_qty_a that deliberately does NOT match fallback_qty, and
    confirm the close signal's qty matches position_qty_a exactly."""
    a, b = _cointegrated_pair(spread_amplitude=50.0)  # blow z far past stop_z
    strat = PairsKellyStrategy(entry_z=1.5, stop_z=3.0, zscore_window=60, min_history=90, fallback_qty=10)
    pair = PairSnapshot("A", "B", a, b, position="long_spread", position_qty_a=37)
    result = strat.evaluate_pair(pair)
    assert result is not None
    assert result.new_position == "none"
    assert result.signal_a.qty == 37, "close must unwind the ACTUAL held qty, not fallback_qty"


def test_close_falls_back_to_fallback_qty_when_position_qty_a_is_unset():
    """A caller bug (position != 'none' but position_qty_a left at its 0
    default) degrades to fallback_qty rather than emitting a qty=0 order
    that would close nothing."""
    a, b = _cointegrated_pair(spread_amplitude=50.0)
    strat = PairsKellyStrategy(entry_z=1.5, stop_z=3.0, zscore_window=60, min_history=90, fallback_qty=10)
    pair = PairSnapshot("A", "B", a, b, position="long_spread", position_qty_a=0)
    result = strat.evaluate_pair(pair)
    assert result is not None
    assert result.signal_a.qty == 10


def test_leg_b_is_scaled_by_qty_a_and_beta_not_a_fixed_constant():
    a, b = _cointegrated_pair(spread_amplitude=6.0)
    strat = PairsKellyStrategy(entry_z=1.5, zscore_window=60, min_history=90, fallback_qty=10)
    result = strat.evaluate_pair(PairSnapshot("A", "B", a, b, position="none"))
    assert result is not None
    expected_qty_b = max(1, round(result.signal_a.qty * result.hedge_ratio))
    assert result.signal_b.qty == expected_qty_b
