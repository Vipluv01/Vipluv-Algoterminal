import numpy as np
import pytest

from app.backtest.paths import clear_path_cache, generate_market_paths, get_market_paths


@pytest.fixture(autouse=True)
def _clear_cache():
    """Every test starts with an empty path cache -- a previous test's
    populated cache must not silently make this one's assertions about
    cache behavior meaningless."""
    clear_path_cache()
    yield
    clear_path_cache()


def test_generates_all_seven_nse_symbols():
    history = generate_market_paths(steps=20, seed=1)
    assert set(history.symbols) == {"ICICIBANK", "HDFCBANK", "RELIANCE", "TCS", "INFY", "SBIN", "TATAMOTORS"}


def test_every_symbol_path_has_exactly_steps_bars():
    history = generate_market_paths(steps=37, seed=1)
    for sym in history.symbols:
        p = history.paths[sym]
        assert len(p.open) == len(p.high) == len(p.low) == len(p.close) == len(p.volume) == 37


def test_open_of_bar_zero_is_the_symbols_own_seed_price():
    history = generate_market_paths(steps=10, seed=1)
    # NAMED_INSTRUMENTS' own starting prices (app/markets.py)
    assert history.paths["ICICIBANK"].open[0] == pytest.approx(1250.0)
    assert history.paths["HDFCBANK"].open[0] == pytest.approx(1650.0)


def test_open_of_bar_t_equals_close_of_bar_t_minus_one():
    """The documented OHLC simplification: no intrabar tick data exists,
    so open[t] is exactly the previous bar's close, not an independently
    observed value."""
    history = generate_market_paths(steps=50, seed=2)
    for sym in history.symbols:
        p = history.paths[sym]
        assert np.array_equal(p.open[1:], p.close[:-1])


def test_high_and_low_bracket_open_and_close_honestly():
    """high/low are exactly the wider of open/close, never fabricating a
    wick beyond what was actually observed."""
    history = generate_market_paths(steps=60, seed=3)
    for sym in history.symbols:
        p = history.paths[sym]
        assert np.array_equal(p.high, np.maximum(p.open, p.close))
        assert np.array_equal(p.low, np.minimum(p.open, p.close))


def test_volume_is_non_negative_and_not_all_zero():
    history = generate_market_paths(steps=100, seed=4)
    for sym in history.symbols:
        p = history.paths[sym]
        assert (p.volume >= 0).all()
        assert p.volume.sum() > 0, f"{sym} traded zero total volume over 100 bars -- suspiciously idle"


def test_close_series_truncates_correctly():
    history = generate_market_paths(steps=40, seed=5)
    full = history.close_series("ICICIBANK")
    truncated = history.close_series("ICICIBANK", upto=10)
    assert len(full) == 40
    assert len(truncated) == 10
    assert np.array_equal(truncated, full[:10])


def test_generation_is_deterministic_given_the_same_steps_and_seed():
    """Two INDEPENDENT calls (bypassing the cache, since generate_market_
    paths is the uncached function) with the same (steps, seed) must
    produce element-for-element identical output -- this is the property
    the CLI's "byte-identical rerun" requirement rests on."""
    h1 = generate_market_paths(steps=150, seed=7)
    h2 = generate_market_paths(steps=150, seed=7)
    for sym in h1.symbols:
        assert np.array_equal(h1.paths[sym].close, h2.paths[sym].close), sym
        assert np.array_equal(h1.paths[sym].volume, h2.paths[sym].volume), sym
        assert np.array_equal(h1.paths[sym].open, h2.paths[sym].open), sym


def test_different_seeds_produce_different_paths():
    h1 = generate_market_paths(steps=100, seed=1)
    h2 = generate_market_paths(steps=100, seed=2)
    assert not np.array_equal(h1.paths["ICICIBANK"].close, h2.paths["ICICIBANK"].close)


def test_get_market_paths_returns_the_same_object_on_repeated_calls():
    """The whole point of the cache: the SAME (steps, seed) combination
    must not be regenerated -- checked by object identity, not just equal
    values, since equal-but-freshly-generated arrays would still mean the
    expensive simulation ran twice."""
    h1 = get_market_paths(steps=30, seed=9)
    h2 = get_market_paths(steps=30, seed=9)
    assert h1 is h2


def test_get_market_paths_generates_separately_for_different_seeds():
    h1 = get_market_paths(steps=30, seed=10)
    h2 = get_market_paths(steps=30, seed=11)
    assert h1 is not h2


def test_get_market_paths_generates_separately_for_different_steps():
    """(steps, seed) is the full cache key -- a different step count must
    not accidentally hit a cached entry generated for a different length."""
    h1 = get_market_paths(steps=30, seed=12)
    h2 = get_market_paths(steps=31, seed=12)
    assert h1 is not h2
    assert len(h1.paths["ICICIBANK"].close) == 30
    assert len(h2.paths["ICICIBANK"].close) == 31


def test_clear_path_cache_forces_regeneration():
    h1 = get_market_paths(steps=25, seed=13)
    clear_path_cache()
    h2 = get_market_paths(steps=25, seed=13)
    assert h1 is not h2
    # Values still match (determinism), just a genuinely new object.
    assert np.array_equal(h1.paths["ICICIBANK"].close, h2.paths["ICICIBANK"].close)


# ---------------------------------------------------------------------------
# Realized volatility band -- must never silently regress (same class of
# guard as bourse's own README-drift test, bench/report_test.go's
# TestReadmeMatchesMeasuredResults).
#
# One bar here is one registry.step_all() call, i.e. one simulated SECOND
# (matches sim/bourse_sim/fundamental.py's own dt=1/(252*6.5*3600) and
# app/backtest/engine.py's BARS_PER_YEAR) -- annualized via sqrt(252*6.5*3600),
# NOT sqrt(252): an earlier bug annualized as if a bar were a trading day,
# reporting 0.01-0.07% instead of the real 2-11%.
#
# These bands are NOT the 15-35% a real NSE equity shows -- they're the
# real, measured, currently-stable range THIS simulation's own microstructure
# produces (see sim/KNOWN_ISSUES.md's "Related: realized volatility is
# near-zero" section for the full investigation: eleven parameters swept,
# including fundamental_sigma at 1000x normal, none of which meaningfully
# move this number). Setting the band to an aspirational target it can't
# reach would make this test either permanently red or a guard nobody
# trusts; an honest band that matches reality is the whole point of a
# regression test. If a future change to app/markets.py's price formation
# genuinely closes this gap, WIDEN these bands to match the new measured
# reality and update sim/KNOWN_ISSUES.md -- don't just loosen them to make
# a failing test pass.
BARS_PER_YEAR = 252 * 6.5 * 3600
REALIZED_VOL_BANDS = {
    # symbol       (min, max) annualized, as a fraction -- roughly 2-2.5x
    # headroom around the measured mean in both directions, tight enough to
    # catch a real regression (e.g. a change that collapses price formation
    # back toward ~0), loose enough to tolerate ordinary seed/step-count
    # variation (measured spread across 5-20 seeds was under 0.3pp).
    "ICICIBANK": (0.03, 0.12),   # measured ~6.8%
    "HDFCBANK": (0.02, 0.09),    # measured ~5.2%
    "RELIANCE": (0.01, 0.06),    # measured ~2.9%
    "TCS": (0.008, 0.05),        # measured ~2.1%
    "INFY": (0.02, 0.08),        # measured ~4.6%
    "SBIN": (0.05, 0.16),        # measured ~10.4%
    "TATAMOTORS": (0.04, 0.13),  # measured ~8.7%
}


def test_realized_vol_lands_in_a_defensible_band_per_symbol():
    history = generate_market_paths(steps=1200, seed=42)
    for sym, (lo, hi) in REALIZED_VOL_BANDS.items():
        closes = history.paths[sym].close
        log_returns = np.diff(np.log(closes))
        annualized = float(log_returns.std() * np.sqrt(BARS_PER_YEAR))
        assert lo <= annualized <= hi, (
            f"{sym}: annualized realized vol {annualized:.4f} outside the defensible "
            f"band [{lo}, {hi}] -- see sim/KNOWN_ISSUES.md's realized-volatility "
            f"section for how this band was measured and why price formation is "
            f"currently this far from a real NSE 15-35% target"
        )
