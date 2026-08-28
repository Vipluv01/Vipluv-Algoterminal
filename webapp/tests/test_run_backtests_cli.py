"""Tests scripts/run_backtests.py's importable functions directly (not by
shelling out to the script, which would be slow at any realistic scale) --
DB writes are isolated to a fresh in-memory SQLite session per test, never
the real on-disk algoterminal.db, the same isolation tests/conftest.py's
`client` fixture already uses for API tests."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import scripts.run_backtests as cli
from app.backtest.paths import clear_path_cache
from app.db import Base
from app.models.backtest import StrategyBacktest

# All 12 strategies registered anywhere in this app (8 equity/arb + 4
# synthetic options, phase 5) -- the sweep must cover every one of them,
# not a subset.
EXPECTED_STRATEGY_KEYS = {
    "alpha_rsi_ema", "momentum_macd", "mean_reversion_bb", "vwap_reversion", "bb_squeeze",
    "pairs_cointegration", "pairs_kelly", "multi_basket",
    "iron_condor", "calendar_spread", "short_strangle", "delta_neutral",
}

# Small enough to run in a reasonable test time, large enough to clear
# every EQUITY/ARB strategy's own min_history (multi_basket's 150 is the
# largest). The 4 OPTIONS strategies are a different story: their
# hold_bars are now real multi-day durations under the corrected
# BARS_PER_YEAR (1 bar == 1 simulated second -- see app.backtest.engine's
# own comment), 117,000 to 468,000 bars. There is no TEST_BARS value that
# both clears that and keeps this test file fast, and there shouldn't be
# one: a 600-bar (10 real minutes) window genuinely cannot evaluate a
# 5-20 real-day options strategy in an honest simulation, regardless of
# test-suite speed goals. So options strategies are EXPECTED to skip at
# this file's TEST_BARS -- see test_run_sweep_persists_one_row_per_non_
# skipped_strategy below, which asserts exactly that split rather than
# assuming (as an earlier version of this file did) that every strategy
# clears every threshold simultaneously.
# TEST_BARS=250, not the 600 an earlier version used: pairs_cointegration/
# pairs_kelly re-run a full cointegration test (an OLS regression) on the
# ENTIRE price history every bar, an O(bars^2)-ish cost per path -- measured
# directly, one full 12-strategy sweep at 600 bars took ~17s, at 250 bars
# ~3.3s, a ~5x difference for less than a 2.5x change in bar count. 250
# still comfortably clears every equity/arb strategy's own min_history
# (multi_basket's 150 is the largest) with real margin.
TEST_PATHS = 2
TEST_BARS = 250
TEST_SEED = 999
EXPECTED_NON_SKIPPED_AT_TEST_BARS = {
    "alpha_rsi_ema", "momentum_macd", "mean_reversion_bb", "vwap_reversion", "bb_squeeze",
    "pairs_cointegration", "pairs_kelly", "multi_basket",
}
EXPECTED_SKIPPED_AT_TEST_BARS = {"iron_condor", "calendar_spread", "short_strangle", "delta_neutral"}


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_path_cache()
    yield
    clear_path_cache()


@pytest.fixture
def isolated_session(monkeypatch):
    """A fresh in-memory DB, with cli.SessionLocal (the script module's OWN
    reference, imported at module load time) monkeypatched to it -- so
    run_sweep's DB writes never touch the real algoterminal.db during
    tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(cli, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


# A full run_sweep(TEST_PATHS, TEST_BARS, TEST_SEED) call is genuinely
# expensive -- ~20+ seconds, dominated by pairs_cointegration/pairs_kelly
# re-running a full Engle-Granger cointegration test (an OLS regression via
# statsmodels) on the ENTIRE price history EVERY bar, an O(bars^2) cost per
# path that has nothing to do with this phase's own changes (it's the
# pairs strategies' own pre-existing per-bar recompute, now proportionally
# dominant because the options strategies correctly skip in ~0 time at this
# TEST_BARS). An earlier version of this file called run_sweep 9 separate
# times across its tests with IDENTICAL (TEST_PATHS, TEST_BARS, TEST_SEED,
# persist_to_db=False) arguments -- 9x the cost for one deterministic
# result computed 9 times. Session-scoped here: every test that only needs
# to INSPECT a no-persist sweep result (not test freshness, determinism, or
# DB side effects) shares this ONE computation instead.
@pytest.fixture(scope="session")
def shared_sweep_result():
    return cli.run_sweep(TEST_PATHS, TEST_BARS, TEST_SEED, persist_to_db=False)


def test_all_twelve_strategies_are_covered():
    adapters = cli._all_strategy_adapters()
    assert set(adapters.keys()) == EXPECTED_STRATEGY_KEYS


def test_run_sweep_produces_a_metrics_result_for_every_strategy(shared_sweep_result):
    results = shared_sweep_result
    assert set(results.keys()) == EXPECTED_STRATEGY_KEYS
    for key, m in results.items():
        assert m.strategy_key == key
        assert m.n_paths == TEST_PATHS
        assert m.n_bars == TEST_BARS
        assert m.base_seed == TEST_SEED


def test_run_sweep_numbers_are_real_not_placeholders(shared_sweep_result):
    """The whole point of this phase: no strategy's Sharpe is a hand-typed
    stand-in, and no fabricated number stands in for "no valid
    measurement" either. Checked here by confirming every value is a
    genuine finite float, a legitimate None (win_rate/profit_factor for a
    strategy that closed zero trades, or sharpe_median/calmar_ratio for a
    strategy whose return series was too degenerate to score -- see
    app.backtest.engine.MAX_PLAUSIBLE_ANNUALIZED_RATIO), or an explicit
    skip -- never a sentinel like exactly 0.0 silently standing in for
    "invalid," and never a NaN leaking through to the persisted/exported
    result."""
    import math

    results = shared_sweep_result
    for key, m in results.items():
        if m.skipped:
            assert m.skip_reason, key
            continue
        assert m.sharpe_median is None or math.isfinite(m.sharpe_median), key
        assert m.sharpe_ci_low is None or math.isfinite(m.sharpe_ci_low), key
        assert m.sharpe_ci_high is None or math.isfinite(m.sharpe_ci_high), key
        assert math.isfinite(m.max_drawdown), key
        assert m.calmar_ratio is None or math.isfinite(m.calmar_ratio), key
        assert m.win_rate is None or math.isfinite(m.win_rate), key
        assert m.profit_factor is None or math.isfinite(m.profit_factor), key
        assert isinstance(m.orders_submitted, int) and m.orders_submitted >= 0, key
        assert isinstance(m.round_trips_closed, int) and m.round_trips_closed >= 0, key
        # A None sharpe/calmar must always carry a real, non-empty reason
        # -- an unexplained missing number is exactly the "quiet, plausible
        # bug" this whole guard exists to prevent.
        if m.sharpe_median is None:
            assert len(m.sharpe_invalid_reasons) > 0, key
        if m.calmar_ratio is None:
            assert len(m.calmar_invalid_reasons) > 0, key


def test_run_sweep_is_deterministic(shared_sweep_result):
    # r1 is the ALREADY-COMPUTED shared result -- only r2 costs a fresh
    # sweep here, not two.
    r1 = shared_sweep_result
    clear_path_cache()  # force genuine regeneration, not a cache hit, for a real determinism proof
    r2 = cli.run_sweep(TEST_PATHS, TEST_BARS, TEST_SEED, persist_to_db=False)

    for key in EXPECTED_STRATEGY_KEYS:
        assert r1[key].sharpe_median == r2[key].sharpe_median, key
        assert r1[key].sharpe_ci_low == r2[key].sharpe_ci_low, key
        assert r1[key].sharpe_ci_high == r2[key].sharpe_ci_high, key
        assert r1[key].orders_submitted == r2[key].orders_submitted, key
        assert r1[key].round_trips_closed == r2[key].round_trips_closed, key
        assert r1[key].max_drawdown == r2[key].max_drawdown, key


def test_run_sweep_persists_one_row_per_non_skipped_strategy(isolated_session):
    # Needs its own persist_to_db=True call -- can't share the no-persist
    # shared_sweep_result, since this test's whole point is the DB side
    # effect that call deliberately skips.
    results = cli.run_sweep(TEST_PATHS, TEST_BARS, TEST_SEED, persist_to_db=True)
    expected_keys = {key for key, m in results.items() if not m.skipped}
    # The 8 equity/arb strategies must all clear TEST_BARS (they always
    # have -- min_history is at most 150 bars); the 4 options strategies
    # must all skip (their real hold_bars, 117k-468k, are nowhere close to
    # TEST_BARS=600 -- see this file's own module-level comment on why
    # that's expected, not a bug). If either set doesn't match, something
    # genuinely regressed -- bump TEST_BARS for the equity side or
    # investigate an options hold_bars change, don't just loosen this.
    assert expected_keys == EXPECTED_NON_SKIPPED_AT_TEST_BARS
    assert {key for key, m in results.items() if m.skipped} == EXPECTED_SKIPPED_AT_TEST_BARS

    db = isolated_session()
    try:
        rows = db.query(StrategyBacktest).all()
        # "Emit NO metrics" for a skipped strategy means no DB row at all --
        # not a row full of nulls -- so the persisted set must match
        # exactly the non-skipped strategies, never more, never fewer.
        assert {r.strategy_key for r in rows} == expected_keys
        for r in rows:
            assert r.n_paths == TEST_PATHS
            assert r.n_bars == TEST_BARS
            assert r.seed == TEST_SEED
            assert r.generated_at is not None
            assert r.n_valid_paths >= 0
            assert r.orders_submitted >= 0
            assert r.round_trips_closed >= 0
    finally:
        db.close()


def test_run_sweep_skips_a_strategy_whose_hold_bars_exceeds_n_bars(isolated_session):
    """delta_neutral's hold_bars is real multi-day scale -- at a much
    shorter n_bars its position can never close within any path, so it
    must be skipped outright (no fabricated metrics, no DB row), not
    scored on an eternally-open position's unrealized drift. Deliberately
    a SMALL n_bars (200, not TEST_BARS' own 600) -- this test is fast
    almost for free since a smaller n_bars means less pairs-cointegration
    per-bar cost too."""
    short_n_bars = 200
    results = cli.run_sweep(TEST_PATHS, short_n_bars, TEST_SEED, persist_to_db=True)

    assert results["delta_neutral"].skipped is True
    assert "hold_bars" in results["delta_neutral"].skip_reason
    assert results["delta_neutral"].sharpe_median is None

    db = isolated_session()
    try:
        assert db.query(StrategyBacktest).filter(StrategyBacktest.strategy_key == "delta_neutral").count() == 0
    finally:
        db.close()


def test_run_sweep_with_persist_false_writes_nothing(isolated_session):
    cli.run_sweep(TEST_PATHS, TEST_BARS, TEST_SEED, persist_to_db=False)
    db = isolated_session()
    try:
        assert db.query(StrategyBacktest).count() == 0
    finally:
        db.close()


def test_write_results_json_produces_valid_json_with_full_provenance(tmp_path, monkeypatch, shared_sweep_result):
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cli, "RESULTS_FILE", tmp_path / "backtests.json")

    results = shared_sweep_result
    cli.write_results_json(results, n_paths=TEST_PATHS, n_bars=TEST_BARS, seed=TEST_SEED)

    with open(tmp_path / "backtests.json") as f:
        payload = json.load(f)

    assert payload["n_paths"] == TEST_PATHS
    assert payload["n_bars"] == TEST_BARS
    assert payload["seed"] == TEST_SEED
    assert "generated_at" in payload
    assert set(payload["strategies"].keys()) == EXPECTED_STRATEGY_KEYS

    for key, entry in payload["strategies"].items():
        assert entry["strategy_key"] == key
        assert "sharpe_median" in entry
        assert "sharpe_ci_low" in entry
        assert "sharpe_ci_high" in entry
        assert "orders_submitted" in entry
        assert "round_trips_closed" in entry
        assert "n_valid_paths" in entry
        assert "n_total_paths" in entry


def test_write_results_json_is_byte_identical_across_reruns_except_timestamp(tmp_path, monkeypatch, shared_sweep_result):
    """The CLI's documented determinism claim, checked directly on the
    exported bytes: everything except generated_at must match exactly
    between two independent runs with the same arguments. r1 is the
    already-computed shared result -- only r2 costs a fresh sweep."""
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cli, "RESULTS_FILE", tmp_path / "run1.json")
    r1 = shared_sweep_result
    cli.write_results_json(r1, n_paths=TEST_PATHS, n_bars=TEST_BARS, seed=TEST_SEED)

    clear_path_cache()
    monkeypatch.setattr(cli, "RESULTS_FILE", tmp_path / "run2.json")
    r2 = cli.run_sweep(TEST_PATHS, TEST_BARS, TEST_SEED, persist_to_db=False)
    cli.write_results_json(r2, n_paths=TEST_PATHS, n_bars=TEST_BARS, seed=TEST_SEED)

    with open(tmp_path / "run1.json") as f:
        payload1 = json.load(f)
    with open(tmp_path / "run2.json") as f:
        payload2 = json.load(f)

    del payload1["generated_at"]
    del payload2["generated_at"]
    assert payload1 == payload2
