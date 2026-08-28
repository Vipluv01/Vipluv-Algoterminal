#!/usr/bin/env python3
"""Runs a Monte Carlo backtest sweep across every registered strategy,
persists the results to the strategy_backtests table, and exports
results/backtests.json -- the artifact the frontend's strategy cards read
provenance from (see app/backtest/monte_carlo.py's module docstring: a
strategy with no completed run has NO Sharpe to show, full stop).

Usage:
    .venv/bin/python scripts/run_backtests.py --paths 30 --bars 2000 --seed 42

Deterministic given (paths, bars, seed): re-running with identical
arguments reproduces the identical numeric result for every strategy
(verified in tests/test_run_backtests_cli.py) -- the underlying path
generation is itself deterministic (app/backtest/paths.py), and nothing in
this script's own aggregation introduces additional randomness beyond
what's seeded through run_monte_carlo. The one field that legitimately
differs between two runs is generated_at (a real wall-clock timestamp,
which is the honest thing for that field to be) -- "byte-identical" refers
to every OTHER field.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.adapters import (
    BacktestStrategy,
    BasketAdapter,
    OptionsBacktestAdapter,
    PairsAdapter,
    SingleInstrumentAdapter,
)
from app.backtest.monte_carlo import StrategyMetrics, run_monte_carlo
from app.db import SessionLocal
from app.migrate import run_migrations
from app.models.backtest import StrategyBacktest
from app.pairs_service import PAIRS_STRATEGY_KEY
from app.strategies.alpha import AlphaRSIEMAStrategy
from app.strategies.bb_squeeze import BBSqueezeStrategy
from app.strategies.calendar_spread import CalendarSpreadStrategy
from app.strategies.delta_neutral import DeltaNeutralStrategy
from app.strategies.iron_condor import IronCondorStrategy
from app.strategies.mean_reversion_bb import MeanReversionBollingerStrategy
from app.strategies.momentum import MomentumMACDStrategy
from app.strategies.multi_basket import BASKET_SYMBOLS, MultiBasketStrategy
from app.strategies.pairs_cointegration import PairsCointegrationStrategy
from app.strategies.pairs_kelly import PairsKellyStrategy
from app.strategies.short_strangle import ShortStrangleStrategy
from app.strategies.vwap_reversion import VWAPReversionStrategy

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
RESULTS_FILE = RESULTS_DIR / "backtests.json"

# One representative symbol for the single-instrument strategies -- the
# sweep evaluates STRATEGY performance, not "which of the 7 NSE symbols is
# best," so one canonical, consistently-used symbol keeps every strategy's
# number comparable to every other's rather than each drawing from a
# different underlying series. ICICIBANK, matching the symbol used
# elsewhere in this codebase's own live smoke tests and pairs_
# cointegration's validated pair.
CANONICAL_SYMBOL = "ICICIBANK"
PAIRS_SYMBOL_A, PAIRS_SYMBOL_B = "ICICIBANK", "HDFCBANK"


def _all_strategy_adapters() -> dict[str, BacktestStrategy]:
    """Builds one fresh adapter per registered strategy -- fresh, not
    shared, because run_monte_carlo calls strategy.reset() between paths
    but a completely separate adapter INSTANCE per strategy avoids any
    possibility of one strategy's run leaving state on an object another
    strategy's run might also reach (they don't share objects at all, so
    there's nothing to leave)."""
    return {
        "alpha_rsi_ema": SingleInstrumentAdapter(AlphaRSIEMAStrategy(), CANONICAL_SYMBOL),
        "momentum_macd": SingleInstrumentAdapter(MomentumMACDStrategy(), CANONICAL_SYMBOL),
        "mean_reversion_bb": SingleInstrumentAdapter(MeanReversionBollingerStrategy(), CANONICAL_SYMBOL),
        "vwap_reversion": SingleInstrumentAdapter(VWAPReversionStrategy(), CANONICAL_SYMBOL),
        "bb_squeeze": SingleInstrumentAdapter(BBSqueezeStrategy(), CANONICAL_SYMBOL),
        PAIRS_STRATEGY_KEY: PairsAdapter(PairsCointegrationStrategy(), PAIRS_SYMBOL_A, PAIRS_SYMBOL_B),
        "pairs_kelly": PairsAdapter(PairsKellyStrategy(), PAIRS_SYMBOL_A, PAIRS_SYMBOL_B),
        "multi_basket": BasketAdapter(MultiBasketStrategy(), BASKET_SYMBOLS),
        "iron_condor": OptionsBacktestAdapter(IronCondorStrategy()),
        "calendar_spread": OptionsBacktestAdapter(CalendarSpreadStrategy()),
        "short_strangle": OptionsBacktestAdapter(ShortStrangleStrategy()),
        "delta_neutral": OptionsBacktestAdapter(DeltaNeutralStrategy()),
    }


def _metrics_to_json(m: StrategyMetrics) -> dict:
    return {
        "strategy_key": m.strategy_key,
        "n_paths": m.n_paths,
        # n_total_paths duplicates n_paths under the name a screen showing
        # "n_valid_paths / n_total_paths" side by side actually wants --
        # kept alongside the original name rather than renaming it, since
        # n_paths is also the sweep's own INPUT parameter (echoed back for
        # provenance) and renaming it would blur "what I asked for" with
        # "what qualified."
        "n_total_paths": m.n_paths,
        "n_valid_paths": m.n_sharpe_valid_paths,
        "n_bars": m.n_bars,
        "base_seed": m.base_seed,
        "sharpe_median": m.sharpe_median,
        "sharpe_ci_low": m.sharpe_ci_low,
        "sharpe_ci_high": m.sharpe_ci_high,
        "win_rate": m.win_rate,
        "max_drawdown": m.max_drawdown,
        "profit_factor": m.profit_factor,
        "calmar_ratio": m.calmar_ratio,
        "orders_submitted": m.orders_submitted,
        "round_trips_closed": m.round_trips_closed,
        "skipped": m.skipped,
        "skip_reason": m.skip_reason,
        # Diagnostic, not display data: how many of n_paths produced a
        # valid (non-degenerate) sharpe/calmar, and why the rest didn't --
        # so "sharpe_median: null" is explainable from the JSON alone.
        # n_sharpe_valid_paths is the SAME number as n_valid_paths above
        # (kept under both names: n_valid_paths for a screen that just
        # wants "how many," n_sharpe_valid_paths/n_calmar_valid_paths for
        # a reader who needs to know sharpe and calmar didn't necessarily
        # agree on which paths were valid).
        "n_sharpe_valid_paths": m.n_sharpe_valid_paths,
        "sharpe_invalid_reasons": list(dict.fromkeys(m.sharpe_invalid_reasons)),  # de-duped, order-preserved
        "n_calmar_valid_paths": m.n_calmar_valid_paths,
        "calmar_invalid_reasons": list(dict.fromkeys(m.calmar_invalid_reasons)),
    }


def run_sweep(n_paths: int, n_bars: int, seed: int, *, persist_to_db: bool = True) -> dict[str, StrategyMetrics]:
    """Runs every registered strategy's Monte Carlo sweep and, unless
    persist_to_db is False (tests use this to avoid touching the real
    on-disk database), writes one StrategyBacktest row per strategy.

    Deliberately no per-strategy path generation here: each adapter, when
    handed to run_monte_carlo, pulls paths from the SAME shared cache
    (app/backtest/paths.get_market_paths) keyed by (n_bars, seed+i) -- so
    running all 8 strategies through this one function generates each
    distinct path once in total, not once per strategy (see paths.py's
    module docstring for the measured IPC cost this avoids).
    """
    adapters = _all_strategy_adapters()
    results: dict[str, StrategyMetrics] = {}

    db = SessionLocal() if persist_to_db else None
    try:
        for strategy_key, adapter in adapters.items():
            print(f"running {strategy_key}: {n_paths} paths x {n_bars} bars (seed={seed})...", file=sys.stderr)
            metrics = run_monte_carlo(adapter, n_paths=n_paths, n_bars=n_bars, base_seed=seed)
            results[strategy_key] = metrics

            if metrics.skipped:
                # "Emit NO metrics for it": a strategy this sweep never
                # actually ran (see run_monte_carlo's insufficient_horizon
                # check) gets no StrategyBacktest row at all -- not a row
                # full of nulls, which would look like "we ran this and it
                # produced nothing" rather than "we correctly declined to
                # run this at these parameters."
                print(f"  SKIPPED {strategy_key}: {metrics.skip_reason}", file=sys.stderr)
                continue

            if metrics.sharpe_median is None:
                reasons = ", ".join(dict.fromkeys(metrics.sharpe_invalid_reasons)) or "no valid paths"
                print(f"  {strategy_key}: no valid Sharpe across {n_paths} paths ({reasons})", file=sys.stderr)

            if db is not None:
                db.add(StrategyBacktest(
                    strategy_key=metrics.strategy_key, n_paths=metrics.n_paths, n_bars=metrics.n_bars,
                    seed=metrics.base_seed, sharpe_median=metrics.sharpe_median,
                    sharpe_ci_low=metrics.sharpe_ci_low, sharpe_ci_high=metrics.sharpe_ci_high,
                    n_valid_paths=metrics.n_sharpe_valid_paths,
                    win_rate=metrics.win_rate, max_drawdown=metrics.max_drawdown,
                    profit_factor=metrics.profit_factor, calmar_ratio=metrics.calmar_ratio,
                    orders_submitted=metrics.orders_submitted, round_trips_closed=metrics.round_trips_closed,
                ))
        if db is not None:
            db.commit()
    finally:
        if db is not None:
            db.close()

    return results


def write_results_json(results: dict[str, StrategyMetrics], *, n_paths: int, n_bars: int, seed: int) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_paths": n_paths,
        "n_bars": n_bars,
        "seed": seed,
        "strategies": {key: _metrics_to_json(m) for key, m in results.items()},
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=int, default=30, help="Monte Carlo paths per strategy (default: 30)")
    parser.add_argument("--bars", type=int, default=2000, help="bars per path (default: 2000)")
    parser.add_argument("--seed", type=int, default=42, help="base seed (default: 42)")
    parser.add_argument("--no-db", action="store_true", help="skip writing to strategy_backtests (JSON export only)")
    args = parser.parse_args()

    if not args.no_db:
        run_migrations()  # ensures strategy_backtests exists before writing to it

    results = run_sweep(args.paths, args.bars, args.seed, persist_to_db=not args.no_db)
    write_results_json(results, n_paths=args.paths, n_bars=args.bars, seed=args.seed)

    print(f"\nwrote {RESULTS_FILE}", file=sys.stderr)
    for key, m in results.items():
        if m.skipped:
            print(f"  {key:20s} SKIPPED -- {m.skip_reason}", file=sys.stderr)
            continue
        win = f"{m.win_rate:.1%}" if m.win_rate is not None else "—"
        pf = f"{m.profit_factor:.2f}" if m.profit_factor is not None else "—"
        if m.sharpe_median is not None:
            sharpe = f"sharpe={m.sharpe_median:+.2f} [{m.sharpe_ci_low:+.2f}, {m.sharpe_ci_high:+.2f}]"
        else:
            reasons = ", ".join(dict.fromkeys(m.sharpe_invalid_reasons)) or "no valid paths"
            sharpe = f"sharpe=— ({reasons})"
        print(
            f"  {key:20s} {sharpe}  win={win}  pf={pf}  "
            f"orders={m.orders_submitted} round_trips={m.round_trips_closed}  "
            f"valid_paths={m.n_sharpe_valid_paths}/{m.n_paths}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
