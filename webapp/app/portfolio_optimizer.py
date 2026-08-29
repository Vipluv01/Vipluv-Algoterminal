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

Solved by SLSQP (scipy.optimize.minimize) over the long-only simplex, not
random-portfolio search -- a real gradient-based solver rather than "try
20,000 candidates and keep the best." _random_search_allocation is kept
alongside specifically so this claim is checked, not just asserted:
tests/test_portfolio_optimizer.py runs both independently and confirms
SLSQP's result is never worse.
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


def _validate_and_prepare(returns: dict[str, np.ndarray]) -> tuple[list[str], np.ndarray, np.ndarray]:
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
    return keys, mean_periodic, cov_periodic


def _portfolio_stats(
    weights: np.ndarray, mean_periodic: np.ndarray, cov_periodic: np.ndarray,
    risk_free_rate: float, periods_per_year: int,
) -> tuple[float, float, float]:
    port_return = float(weights @ mean_periodic * periods_per_year)
    port_var = float(weights @ cov_periodic @ weights * periods_per_year)
    port_vol = float(np.sqrt(max(port_var, 0.0)))
    sharpe = (port_return - risk_free_rate) / port_vol if port_vol > 0 else -np.inf
    return port_return, port_vol, sharpe


def _random_search_allocation(
    mean_periodic: np.ndarray, cov_periodic: np.ndarray, *,
    risk_free_rate: float, periods_per_year: int, n_random_portfolios: int, seed: int,
) -> tuple[np.ndarray, float, float, float]:
    """The original solver, kept as an independent cross-check: generate
    n_random_portfolios Dirichlet-distributed candidates over the simplex,
    score each one, keep the best. Precision beyond a few thousand samples
    buys nothing here on its own terms -- its purpose now is validating
    SLSQP, not being the production path, so a bigger sample specifically
    makes it a STRONGER check, not a better optimizer to ship.

    Returns (weights, expected_return, expected_volatility, sharpe_ratio).
    """
    n = len(mean_periodic)
    rng = np.random.default_rng(seed)
    raw = rng.dirichlet(np.ones(n), size=n_random_portfolios)  # each row sums to 1, all >= 0

    port_return = raw @ mean_periodic * periods_per_year
    port_var = np.einsum("ij,jk,ik->i", raw, cov_periodic, raw) * periods_per_year
    port_vol = np.sqrt(np.maximum(port_var, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(port_vol > 0, (port_return - risk_free_rate) / port_vol, -np.inf)

    best = int(np.argmax(sharpe))
    return raw[best], float(port_return[best]), float(port_vol[best]), float(sharpe[best])


def _slsqp_allocation(
    mean_periodic: np.ndarray, cov_periodic: np.ndarray, *,
    risk_free_rate: float, periods_per_year: int,
) -> tuple[np.ndarray, float, float, float]:
    """Maximizes Sharpe (minimizes its negative) over the long-only
    simplex: sum(w)=1, w>=0.

    Multi-start, not a single call: Sharpe-over-a-simplex is not
    guaranteed convex for an arbitrary covariance matrix, so SLSQP (a
    local solver) can in principle settle into a local optimum. Starting
    from equal-weight AND from every single-asset corner of the simplex is
    cheap (SLSQP converges in a handful of iterations per start on a
    problem this small) and is what makes "SLSQP's result is never worse
    than a 20,000-sample random search" an earned claim rather than a
    lucky one -- verified directly in tests/test_portfolio_optimizer.py,
    not just asserted here.
    """
    # Lazy, not module top-level -- scipy is real import weight (Render
    # memory investigation) a process that never actually runs the
    # optimizer (most requests won't) shouldn't pay for. Cheap on every
    # call after the first: scipy.optimize is cached in sys.modules by then.
    from scipy.optimize import minimize

    n = len(mean_periodic)

    def negative_sharpe(w: np.ndarray) -> float:
        _, _, sharpe = _portfolio_stats(w, mean_periodic, cov_periodic, risk_free_rate, periods_per_year)
        return -sharpe if np.isfinite(sharpe) else 1e6  # unattainable "worst" score, not -inf into the solver

    bounds = [(0.0, 1.0)] * n
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

    starts = [np.full(n, 1.0 / n)] + [np.eye(n)[i] for i in range(n)]

    best_weights, best_sharpe = None, -np.inf
    for x0 in starts:
        result = minimize(
            negative_sharpe, x0, method="SLSQP", bounds=bounds, constraints=constraints,
            options={"maxiter": 200, "ftol": 1e-12},
        )
        if not result.success:
            continue
        # SLSQP can return weights with tiny negative components (e.g.
        # -1e-16) from floating-point slack at the w>=0 boundary -- clip
        # and renormalize rather than let that leak into a "negative
        # weight" a caller would have to defend against.
        w = np.clip(result.x, 0.0, None)
        total = w.sum()
        if total <= 0:
            continue
        w = w / total
        sharpe = -negative_sharpe(w)
        if sharpe > best_sharpe:
            best_weights, best_sharpe = w, sharpe

    if best_weights is None:
        raise RuntimeError("SLSQP failed to converge from every starting point")

    port_return, port_vol, sharpe = _portfolio_stats(best_weights, mean_periodic, cov_periodic, risk_free_rate, periods_per_year)
    return best_weights, port_return, port_vol, sharpe


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

    n_random_portfolios and seed are accepted for backward-compatible
    signature parity with the previous random-search-only version (some
    callers may still pass them), but no longer affect this function's
    OWN result -- SLSQP is deterministic given the inputs. They still
    matter if you call _random_search_allocation directly.
    """
    keys, mean_periodic, cov_periodic = _validate_and_prepare(returns)

    weights, port_return, port_vol, sharpe = _slsqp_allocation(
        mean_periodic, cov_periodic, risk_free_rate=risk_free_rate, periods_per_year=periods_per_year,
    )

    return OptimizationResult(
        strategy_keys=keys,
        weights=weights,
        expected_return=port_return,
        expected_volatility=port_vol,
        sharpe_ratio=sharpe,
    )
