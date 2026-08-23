"""Allocates weight across the 5 strategies by mean-variance optimization
(max-Sharpe, Markowitz 1952) over their historical/paper return streams.

Deliberately the boring, textbook version -- no shrinkage, no Black-Litterman,
no leverage. The 94%-win-rate lesson from the old Algo Terminal wasn't
"the math was too simple," it was "the underlying signal (correlation, not
cointegration) was wrong, and nobody looked past the win rate." Sophisticated
portfolio math sitting on top of unvalidated strategy returns would just be
the same mistake one layer up -- so the honest move here is standard,
auditable math over signals that have ALREADY been separately validated
(see the strategies/ package's own tests), not a fancier optimizer.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class OptimizationResult:
    strategy_keys: list[str]
    weights: np.ndarray  # sums to 1.0, all >= 0 (long-only)
    expected_return: float   # annualized
    expected_volatility: float  # annualized
    sharpe_ratio: float


def max_sharpe_allocation(
    returns: dict[str, np.ndarray],
    *,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
    n_random_portfolios: int = 20_000,
    seed: int = 0,
) -> OptimizationResult:
    """returns: {strategy_key: array of periodic returns, same length and
    same period for every strategy}.

    Solved by random-portfolio search over the long-only simplex rather
    than a closed-form/quadratic-programming solver: for 5 strategies this
    converges to essentially the same answer, needs no extra optimization
    dependency (cvxpy/scipy.optimize would be the alternative), and the
    method itself is trivial to audit by eye -- generate candidates, score
    each one, keep the best. Precision beyond a few thousand samples buys
    nothing here; the input return estimates themselves are far noisier
    than that.
    """
    keys = list(returns.keys())
    if len(keys) < 2:
        raise ValueError("need at least 2 strategies to optimize an allocation across")

    lengths = {len(v) for v in returns.values()}
    if len(lengths) != 1:
        raise ValueError(f"all strategies' return series must be the same length, got {lengths}")

    R = np.column_stack([returns[k] for k in keys])  # (n_periods, n_strategies)
    mean_periodic = R.mean(axis=0)
    cov_periodic = np.cov(R, rowvar=False)
    if cov_periodic.ndim == 0:  # exactly 2 strategies with scalar cov edge case
        cov_periodic = np.array([[cov_periodic]])

    rng = np.random.default_rng(seed)
    raw = rng.dirichlet(np.ones(len(keys)), size=n_random_portfolios)  # each row sums to 1, all >= 0

    port_return = raw @ mean_periodic * periods_per_year
    port_var = np.einsum("ij,jk,ik->i", raw, cov_periodic, raw) * periods_per_year
    port_vol = np.sqrt(np.maximum(port_var, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(port_vol > 0, (port_return - risk_free_rate) / port_vol, -np.inf)

    best = int(np.argmax(sharpe))
    return OptimizationResult(
        strategy_keys=keys,
        weights=raw[best],
        expected_return=float(port_return[best]),
        expected_volatility=float(port_vol[best]),
        sharpe_ratio=float(sharpe[best]),
    )
