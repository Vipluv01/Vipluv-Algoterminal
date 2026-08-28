import numpy as np
import pytest

from app.quant.attribution import BENCHMARK_SYMBOLS, brinson_attribution


def _random_full_weights(rng: np.random.Generator, symbols: list[str]) -> dict[str, float]:
    raw = rng.random(len(symbols))
    raw = raw / raw.sum()
    return dict(zip(symbols, raw))


# ---------------------------------------------------------------------------
# The identity: allocation + selection + interaction == R_p - B, exactly
# ---------------------------------------------------------------------------

def test_identity_holds_across_random_weights_and_returns():
    """The core algebraic claim, checked over many random trials rather
    than one -- if it holds it should hold everywhere, and a single
    lucky-cancellation trial would be weak evidence."""
    rng = np.random.default_rng(0)
    symbols = list(BENCHMARK_SYMBOLS)
    max_diff = 0.0
    for _ in range(200):
        w = _random_full_weights(rng, symbols)
        R = {s: rng.normal(0, 0.05) for s in symbols}
        B = {s: rng.normal(0, 0.05) for s in symbols}
        result = brinson_attribution(w, R, B)
        diff = abs(result.excess - (result.portfolio_return - result.benchmark_return))
        max_diff = max(max_diff, diff)
    assert max_diff < 1e-9


def test_identity_holds_with_custom_non_equal_weight_benchmark():
    """Same identity, but against a benchmark that ISN'T the default
    equal-weight basket -- confirms the identity isn't accidentally
    relying on BENCHMARK_WEIGHT's specific value."""
    rng = np.random.default_rng(1)
    symbols = ["A", "B", "C", "D"]
    raw_bw = rng.random(4)
    benchmark_weights = dict(zip(symbols, raw_bw / raw_bw.sum()))
    w = _random_full_weights(rng, symbols)
    R = {s: rng.normal(0, 0.05) for s in symbols}
    B = {s: rng.normal(0, 0.05) for s in symbols}

    result = brinson_attribution(w, R, B, benchmark_weights=benchmark_weights)
    assert result.excess == pytest.approx(result.portfolio_return - result.benchmark_return, abs=1e-9)


def test_identity_holds_when_portfolio_and_benchmark_universes_differ():
    """Portfolio holds a symbol the benchmark doesn't (and vice versa) --
    the identity must still hold exactly, since real strategies often
    trade outside a fixed benchmark universe."""
    rng = np.random.default_rng(2)
    benchmark_weights = {"A": 0.5, "B": 0.5}
    w = {"A": 0.3, "C": 0.7}  # C isn't in the benchmark at all; B isn't held
    R = {"A": 0.02, "C": -0.01}
    B = {"A": 0.015, "B": 0.03}  # C's benchmark return doesn't exist -> treated as 0

    result = brinson_attribution(w, R, B, benchmark_weights=benchmark_weights)
    assert result.excess == pytest.approx(result.portfolio_return - result.benchmark_return, abs=1e-9)


# ---------------------------------------------------------------------------
# Weight validation
# ---------------------------------------------------------------------------

def test_rejects_portfolio_weights_that_dont_sum_to_one():
    """Not a convenience default -- the identity is only exact when weights
    sum to 1 (see brinson_attribution's docstring: verified directly that
    a 50%-cash portfolio run through the formula unadjusted breaks the
    identity by a term proportional to B*(1 - sum(w))). Silently allowing
    it would produce a plausible-looking but WRONG excess figure."""
    w = {"ICICIBANK": 0.3, "HDFCBANK": 0.2}  # sums to 0.5
    R = {"ICICIBANK": 0.02, "HDFCBANK": 0.01}
    B = {s: 0.01 for s in BENCHMARK_SYMBOLS}
    with pytest.raises(ValueError, match="sum to 1"):
        brinson_attribution(w, R, B)


def test_rejects_benchmark_weights_that_dont_sum_to_one():
    w = {"A": 1.0}
    R = {"A": 0.02}
    B = {"A": 0.01, "B": 0.01}
    with pytest.raises(ValueError, match="sum to 1"):
        brinson_attribution(w, R, B, benchmark_weights={"A": 0.3, "B": 0.3})


def test_accepts_weights_within_floating_point_tolerance_of_one():
    """Weights computed from real position sizes will rarely sum to
    EXACTLY 1.0 due to floating-point arithmetic -- must not reject a
    portfolio for a 1e-12 rounding artifact."""
    w = {"A": 1.0 / 3, "B": 1.0 / 3, "C": 1.0 / 3}  # sums to 0.9999999999999999
    R = {"A": 0.01, "B": 0.02, "C": 0.03}
    B = {"A": 0.01, "B": 0.01, "C": 0.01}
    result = brinson_attribution(w, R, B, benchmark_weights={"A": 1 / 3, "B": 1 / 3, "C": 1 / 3})
    assert result.portfolio_return == pytest.approx(0.02, abs=1e-9)


# ---------------------------------------------------------------------------
# Known-direction effects
# ---------------------------------------------------------------------------

def test_positive_allocation_effect_from_overweighting_an_outperformer():
    """Overweight the ONE symbol that beats the benchmark average, hold
    every symbol at exactly its own benchmark return (so selection and
    interaction are both exactly zero by construction) -- the entire
    excess return must come through as a POSITIVE allocation effect."""
    symbols = list(BENCHMARK_SYMBOLS)
    B = {s: 0.01 for s in symbols}
    B["TCS"] = 0.05  # TCS outperforms every other constituent

    overweight = 0.30
    remainder = (1 - overweight) / (len(symbols) - 1)
    w = {s: remainder for s in symbols}
    w["TCS"] = overweight

    R = dict(B)  # portfolio earns exactly each symbol's benchmark return

    result = brinson_attribution(w, R, B)
    assert result.allocation > 0
    assert result.selection == pytest.approx(0.0, abs=1e-12)
    assert result.interaction == pytest.approx(0.0, abs=1e-12)
    assert result.excess == pytest.approx(result.allocation, abs=1e-9)


def test_negative_allocation_effect_from_underweighting_an_outperformer():
    """The mirror case: underweight (down to zero) the one outperforming
    symbol -- allocation effect must be negative."""
    symbols = list(BENCHMARK_SYMBOLS)
    B = {s: 0.01 for s in symbols}
    B["TCS"] = 0.05

    remainder = 1.0 / (len(symbols) - 1)
    w = {s: remainder for s in symbols}
    w["TCS"] = 0.0

    R = {s: B[s] for s in symbols if s != "TCS"}  # TCS not held, no realized return to report

    result = brinson_attribution(w, R, B)
    assert result.allocation < 0


def test_positive_selection_effect_from_beating_the_benchmark_at_equal_weight():
    """Hold the benchmark's OWN weights exactly (allocation and
    interaction both zero by construction -- w_i == W_i everywhere makes
    every (w_i - W_i) term vanish), but beat the benchmark's per-symbol
    return on one holding -- the excess must show up entirely as positive
    selection."""
    symbols = list(BENCHMARK_SYMBOLS)
    B = {s: 0.01 for s in symbols}
    w = {s: 1.0 / len(symbols) for s in symbols}  # exactly equal-weight, same as the benchmark

    R = dict(B)
    R["TCS"] = 0.08  # this portfolio picked TCS and it outperformed ITS OWN benchmark return

    result = brinson_attribution(w, R, B)
    assert result.allocation == pytest.approx(0.0, abs=1e-12)
    assert result.interaction == pytest.approx(0.0, abs=1e-12)
    assert result.selection > 0
    assert result.excess == pytest.approx(result.selection, abs=1e-9)


def test_zero_excess_when_portfolio_exactly_replicates_the_benchmark():
    """The trivial case: portfolio weights AND returns exactly match the
    benchmark -- every effect and the total excess must be exactly zero."""
    symbols = list(BENCHMARK_SYMBOLS)
    B = {s: 0.02 for s in symbols}
    w = {s: 1.0 / len(symbols) for s in symbols}
    R = dict(B)

    result = brinson_attribution(w, R, B)
    assert result.allocation == pytest.approx(0.0, abs=1e-12)
    assert result.selection == pytest.approx(0.0, abs=1e-12)
    assert result.interaction == pytest.approx(0.0, abs=1e-12)
    assert result.excess == pytest.approx(0.0, abs=1e-12)
