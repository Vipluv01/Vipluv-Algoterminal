"""Brinson-Fachler single-period performance attribution: decomposes a
portfolio's excess return over a benchmark into allocation, selection, and
interaction effects.

Pure decomposition math, no data-fetching or execution here -- callers
supply portfolio weights and per-asset returns (however they were
computed: from the accounting ledger's realizations, from a backtest run,
whatever) and get the attribution breakdown back.
"""

from __future__ import annotations

from dataclasses import dataclass

# The standard benchmark for this app: an equal-weight buy-and-hold basket
# of the 7 NSE instruments app/markets.py trades (see NAMED_INSTRUMENTS
# there). Attribution is meaningless without naming what it's measured
# against -- this constant IS that name, and every attribution result
# computed with it should be presented alongside this fact, not silently.
BENCHMARK_SYMBOLS = ("ICICIBANK", "HDFCBANK", "RELIANCE", "TCS", "INFY", "SBIN", "TATAMOTORS")
BENCHMARK_WEIGHT = 1.0 / len(BENCHMARK_SYMBOLS)


@dataclass(frozen=True)
class AttributionResult:
    """Per Brinson-Fachler (1985):

    allocation   = sum_i (w_i - W_i) * (B_i - B)   -- return from over/under-
                   weighting a sector relative to the benchmark, independent
                   of any stock-picking skill within it
    selection    = sum_i W_i * (R_i - B_i)         -- return from picking
                   assets that beat their own benchmark-weight contribution,
                   independent of how much was allocated to them
    interaction  = sum_i (w_i - W_i) * (R_i - B_i) -- the cross term: extra
                   return from being simultaneously overweight AND right (or
                   underweight and wrong) -- not attributable to allocation
                   or selection skill alone
    excess       = allocation + selection + interaction == R_p - B, exactly
                   (verified as an identity, not an approximation, in
                   tests/test_attribution.py)

    portfolio_return / benchmark_return are the aggregate R_p and B this
    decomposition reproduces the excess of -- included so a caller doesn't
    have to recompute them separately to sanity-check excess == R_p - B.
    """

    allocation: float
    selection: float
    interaction: float
    excess: float
    portfolio_return: float
    benchmark_return: float


WEIGHT_SUM_TOLERANCE = 1e-6


def brinson_attribution(
    portfolio_weights: dict[str, float],
    asset_returns: dict[str, float],
    benchmark_returns: dict[str, float],
    *,
    benchmark_weights: dict[str, float] | None = None,
) -> AttributionResult:
    """Decomposes portfolio excess return over the benchmark.

    portfolio_weights: w_i, this portfolio's actual weight in each symbol.
        MUST sum to exactly 1 (within WEIGHT_SUM_TOLERANCE) -- see the note
        on cash below for why this is a hard requirement, not a
        convenience default. A symbol absent from this dict is treated as
        w_i=0.
    asset_returns: R_i, this portfolio's realized return on each symbol
        it actually holds (only needed for symbols with portfolio_weights
        != 0).
    benchmark_returns: B_i, the benchmark's return on EVERY benchmark
        constituent (needed for all of them, since the allocation and
        interaction effects are computed over the full benchmark universe,
        not just what the portfolio happens to hold).
    benchmark_weights: W_i, defaults to equal-weight over
        BENCHMARK_SYMBOLS (1/7 each) if not given -- the standard
        benchmark this app measures every strategy against. Must also sum
        to 1.

    On cash: an idle, uninvested balance is NOT free to omit. The
    algebraic identity allocation + selection + interaction == R_p - B
    only holds when sum(w_i) == sum(W_i) == 1 (this is a real constraint
    of the Brinson-Fachler decomposition, not an implementation choice --
    verified directly: a portfolio at 50% invested, 50% idle cash, passed
    through this formula unadjusted, breaks the identity by a term
    proportional to B * (1 - sum(w_i))). A caller with real idle cash must
    include it explicitly as its own entry (e.g. "CASH": 0.30) with a
    matching zero-or-risk-free return in asset_returns, so weights still
    sum to 1 -- the standard treatment for a partially-invested portfolio
    under Brinson-Fachler.

    Every symbol that appears in EITHER the portfolio or the benchmark
    universe is included in the sums (a symbol the portfolio holds but the
    benchmark doesn't contributes an allocation/interaction effect via
    B_i=0, W_i=0; a symbol only in the benchmark contributes via w_i=0,
    R_i=0).
    """
    if benchmark_weights is None:
        benchmark_weights = {sym: BENCHMARK_WEIGHT for sym in BENCHMARK_SYMBOLS}

    portfolio_sum = sum(portfolio_weights.values())
    if abs(portfolio_sum - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"portfolio_weights must sum to 1.0, got {portfolio_sum} -- "
            "include idle cash as its own entry with a matching return "
            "(see brinson_attribution's docstring on why this is required "
            "for the allocation+selection+interaction identity to hold)"
        )
    benchmark_sum = sum(benchmark_weights.values())
    if abs(benchmark_sum - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"benchmark_weights must sum to 1.0, got {benchmark_sum}")

    all_symbols = set(portfolio_weights) | set(benchmark_weights)

    allocation = 0.0
    selection = 0.0
    interaction = 0.0
    portfolio_return = 0.0
    benchmark_return = 0.0

    benchmark_agg = sum(benchmark_weights.get(sym, 0.0) * benchmark_returns.get(sym, 0.0) for sym in all_symbols)

    for sym in all_symbols:
        w_i = portfolio_weights.get(sym, 0.0)
        W_i = benchmark_weights.get(sym, 0.0)
        R_i = asset_returns.get(sym, 0.0)
        B_i = benchmark_returns.get(sym, 0.0)

        allocation += (w_i - W_i) * (B_i - benchmark_agg)
        selection += W_i * (R_i - B_i)
        interaction += (w_i - W_i) * (R_i - B_i)
        portfolio_return += w_i * R_i

    benchmark_return = benchmark_agg
    excess = allocation + selection + interaction

    return AttributionResult(
        allocation=allocation,
        selection=selection,
        interaction=interaction,
        excess=excess,
        portfolio_return=portfolio_return,
        benchmark_return=benchmark_return,
    )
