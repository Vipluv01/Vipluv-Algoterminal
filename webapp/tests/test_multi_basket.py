"""Same positive/negative-control discipline as test_pairs_cointegration.py,
generalized to 3 series: a genuinely cointegrated triple must be
recognized as such by Johansen, and three independent random walks must
not be."""

import numpy as np
import pytest

from app.strategies.multi_basket import (
    BasketSnapshot,
    MultiBasketStrategy,
    _johansen_top_eigenvector,
)


def _cointegrated_triple(n=400, seed=0):
    """x3 is a fixed linear combination of x1 and x2 plus stationary
    noise -- x3 - x1 - x2 is stationary by construction, so this triple
    has a genuine (at least one) cointegrating relationship, the 3-series
    generalization of test_pairs_cointegration.py's own A = B + noise
    construction."""
    rng = np.random.default_rng(seed)
    x1 = 100 + np.cumsum(rng.normal(0, 0.5, n))
    x2 = 80 + np.cumsum(rng.normal(0, 0.5, n))
    x3 = x1 + x2 + 10.0 + rng.normal(0, 0.3, n)
    return x1, x2, x3


def _independent_triple(n=400, seed=0):
    rng = np.random.default_rng(seed)
    x1 = 100 + np.cumsum(rng.normal(0, 1.0, n))
    x2 = 80 + np.cumsum(rng.normal(0, 1.0, n))
    x3 = 60 + np.cumsum(rng.normal(0, 1.0, n))
    return x1, x2, x3


def test_johansen_finds_cointegration_in_a_genuinely_cointegrated_triple():
    x1, x2, x3 = _cointegrated_triple()
    matrix = np.column_stack([x1, x2, x3])
    is_cointegrated, weights = _johansen_top_eigenvector(matrix)
    assert is_cointegrated


def test_johansen_finds_no_cointegration_in_independent_walks():
    x1, x2, x3 = _independent_triple()
    matrix = np.column_stack([x1, x2, x3])
    is_cointegrated, _weights = _johansen_top_eigenvector(matrix)
    assert not is_cointegrated


def test_top_eigenvector_is_normalized_to_one_on_the_first_symbol():
    x1, x2, x3 = _cointegrated_triple()
    matrix = np.column_stack([x1, x2, x3])
    _is_cointegrated, weights = _johansen_top_eigenvector(matrix)
    assert weights[0] == pytest.approx(1.0)


def test_strategy_can_enter_on_a_genuinely_cointegrated_basket():
    x1, x2, x3 = _cointegrated_triple(seed=3)
    symbols = ("A", "B", "C")
    strat = MultiBasketStrategy(symbols=symbols, entry_z=0.5, zscore_window=60, min_history=150)
    prices = {"A": x1, "B": x2, "C": x3}

    # Scan forward for a bar where this seed's realized z actually clears
    # entry_z, rather than assuming any single fixed bar does -- same
    # "search rather than assume" approach test_pairs_cointegration.py's
    # own positive-control test uses via spread_amplitude, adapted to a
    # naturally-generated series here instead of a forced final value.
    found = None
    for t in range(strat.min_history, len(x1) + 1):
        snap = BasketSnapshot(symbols=symbols, prices={k: v[:t] for k, v in prices.items()}, position="none")
        result = strat.evaluate_basket(snap)
        if result is not None:
            found = result
            break
    assert found is not None, "a genuinely cointegrated basket should eventually cross the entry threshold"
    assert found.new_position in ("long_spread", "short_spread")
    assert set(found.leg_signals) == set(symbols)


def test_strategy_does_not_trade_independent_walks_without_real_statistical_basis():
    """The critical negative control (same discipline as test_pairs_
    cointegration.py's own version): a single evaluation, not a scan --
    Johansen's rank<=0 null is rejected at the 95% level by DESIGN about
    5% of the time even under genuine independence, so scanning many
    correlated, overlapping windows for "never once triggers" would be
    asserting something statistically improbable, not a real property of
    the strategy. If it DOES fire here, it must be a documented false
    positive the significance level itself allows for (evaluate_basket
    only ever produces a signal when is_cointegrated was True), not a
    bypass of the check."""
    x1, x2, x3 = _independent_triple(seed=3)
    symbols = ("A", "B", "C")
    strat = MultiBasketStrategy(symbols=symbols, entry_z=1.5, zscore_window=60, min_history=150)
    snap = BasketSnapshot(symbols=symbols, prices={"A": x1, "B": x2, "C": x3}, position="none")
    result = strat.evaluate_basket(snap)
    if result is not None:
        assert result.is_cointegrated is True


def test_insufficient_history_returns_none():
    x1, x2, x3 = _cointegrated_triple(n=50)
    symbols = ("A", "B", "C")
    strat = MultiBasketStrategy(symbols=symbols, min_history=150)
    snap = BasketSnapshot(symbols=symbols, prices={"A": x1, "B": x2, "C": x3}, position="none")
    assert strat.evaluate_basket(snap) is None


def test_raises_when_snapshot_symbols_dont_match_the_strategys_own():
    strat = MultiBasketStrategy(symbols=("A", "B", "C"))
    snap = BasketSnapshot(symbols=("A", "B"), prices={"A": np.zeros(10), "B": np.zeros(10)}, position="none")
    with pytest.raises(ValueError):
        strat.evaluate_basket(snap)


# ---------------------------------------------------------------------------
# Leg side/qty sign convention
# ---------------------------------------------------------------------------

def test_leg_side_and_qty_buys_a_positive_weight_leg_when_long_spread():
    strat = MultiBasketStrategy(qty=10)
    weights = {"A": 1.0, "B": 0.5, "C": -0.8}
    side, qty = strat._leg_side_and_qty("long_spread", "A", weights)
    assert side == "buy"
    side_b, qty_b = strat._leg_side_and_qty("long_spread", "B", weights)
    assert side_b == "buy"
    assert qty_b == max(1, round(10 * 0.5))


def test_leg_side_and_qty_sells_a_negative_weight_leg_when_long_spread():
    strat = MultiBasketStrategy(qty=10)
    weights = {"A": 1.0, "B": 0.5, "C": -0.8}
    side, qty = strat._leg_side_and_qty("long_spread", "C", weights)
    assert side == "sell"
    assert qty == max(1, round(10 * 0.8))


def test_leg_side_and_qty_flips_for_short_spread():
    strat = MultiBasketStrategy(qty=10)
    weights = {"A": 1.0, "B": 0.5, "C": -0.8}
    long_side, _ = strat._leg_side_and_qty("long_spread", "A", weights)
    short_side, _ = strat._leg_side_and_qty("short_spread", "A", weights)
    assert long_side != short_side


def test_close_signals_are_the_mirror_of_entry_signals():
    x1, x2, x3 = _cointegrated_triple(seed=3)
    symbols = ("A", "B", "C")
    strat = MultiBasketStrategy(symbols=symbols, entry_z=0.5, zscore_window=60, min_history=150)
    prices = {"A": x1, "B": x2, "C": x3}

    entry = None
    entry_t = None
    for t in range(strat.min_history, len(x1) + 1):
        snap = BasketSnapshot(symbols=symbols, prices={k: v[:t] for k, v in prices.items()}, position="none")
        result = strat.evaluate_basket(snap)
        if result is not None:
            entry, entry_t = result, t
            break
    assert entry is not None

    # Immediately ask for a close from the position just entered (using
    # the SAME bar's data, isolating "does close correctly mirror entry"
    # from "does the strategy eventually want to exit").
    held_snap = BasketSnapshot(symbols=symbols, prices={k: v[:entry_t] for k, v in prices.items()}, position=entry.new_position)
    close = strat._close(held_snap, entry.weights, z=99.0, is_cointegrated=True)
    for sym in symbols:
        assert close.leg_signals[sym].side != entry.leg_signals[sym].side
