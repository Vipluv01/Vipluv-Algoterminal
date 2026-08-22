"""Validates a simulated price series against well-documented empirical
regularities of real financial markets ("stylized facts", Cont 2001).

This is the actual proof that "price emerges from order flow" is true here
rather than merely claimed. The original flawed design (GBM setting the
traded price directly) would FAIL every one of these tests by construction:
GBM returns are, by definition, i.i.d. Gaussian -- no fat tails, no
volatility clustering, nothing for this module to find. A price series that
emerges from heterogeneous agents trading against a real order book can
plausibly show these regularities; one that's just relabeled noise cannot.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats as sstats


def resample_to_grid(
    trade_prices: np.ndarray, trade_times: np.ndarray, n_steps: int
) -> np.ndarray:
    """One price per simulation step: the last trade in that step, or the
    previous grid price carried forward if nothing traded. Necessary because
    trades happen at irregular counts per step, and every statistic below
    needs a regularly-spaced series."""
    grid = np.full(n_steps, np.nan)
    for t, px in zip(trade_times, trade_prices):
        grid[t] = px  # last write per step wins -> last trade of that step
    # Forward-fill gaps.
    last = grid[0] if not np.isnan(grid[0]) else trade_prices[0]
    for i in range(n_steps):
        if np.isnan(grid[i]):
            grid[i] = last
        else:
            last = grid[i]
    return grid


def log_returns(price_grid: np.ndarray) -> np.ndarray:
    return np.diff(np.log(price_grid))


@dataclass(frozen=True)
class StylizedFactsReport:
    n_returns: int
    excess_kurtosis: float
    kurtosis_pvalue: float          # H0: normal kurtosis (3); low p => fat tails
    return_autocorr_lag1: float
    return_autocorr_pvalue: float   # H0: zero autocorrelation
    abs_return_autocorr_lag1: float
    abs_return_autocorr_pvalue: float  # H0: zero -- low p + positive => vol clustering

    @property
    def has_fat_tails(self) -> bool:
        return self.excess_kurtosis > 0 and self.kurtosis_pvalue < 0.05

    @property
    def returns_are_weakly_autocorrelated(self) -> bool:
        """Real markets show near-zero (not exactly zero) raw return
        autocorrelation -- easy predictability at lag 1 would be an
        arbitrage. 'Weak' is checked as |autocorr| staying small, not
        necessarily insignificant, since with enough samples even a tiny
        true autocorrelation becomes statistically detectable."""
        return abs(self.return_autocorr_lag1) < 0.15

    @property
    def has_volatility_clustering(self) -> bool:
        return self.abs_return_autocorr_lag1 > 0 and self.abs_return_autocorr_pvalue < 0.05

    def summary(self) -> str:
        lines = [
            f"n_returns = {self.n_returns}",
            f"[{'PASS' if self.has_fat_tails else 'FAIL'}] fat tails: "
            f"excess kurtosis = {self.excess_kurtosis:.2f} (p={self.kurtosis_pvalue:.4f}, real markets: >0, often 3-10+)",
            f"[{'PASS' if self.returns_are_weakly_autocorrelated else 'FAIL'}] weak return autocorrelation: "
            f"lag-1 = {self.return_autocorr_lag1:.4f} (p={self.return_autocorr_pvalue:.4f}, real markets: near 0)",
            f"[{'PASS' if self.has_volatility_clustering else 'FAIL'}] volatility clustering: "
            f"|return| lag-1 autocorr = {self.abs_return_autocorr_lag1:.4f} (p={self.abs_return_autocorr_pvalue:.4f}, real markets: positive, significant)",
        ]
        return "\n".join(lines)


def _autocorr_lag1_with_pvalue(x: np.ndarray) -> tuple[float, float]:
    """Pearson correlation between x[:-1] and x[1:], with its own
    significance test -- this is the standard lag-1 sample autocorrelation,
    tested the same way any Pearson r is tested."""
    if len(x) < 3:
        return 0.0, 1.0
    r, p = sstats.pearsonr(x[:-1], x[1:])
    return float(r), float(p)


def analyze(price_grid: np.ndarray) -> StylizedFactsReport:
    rets = log_returns(price_grid)
    rets = rets[np.isfinite(rets)]

    # Excess kurtosis (Fisher: normal = 0) via scipy, with its own
    # significance test against the normal-distribution null.
    kurt = float(sstats.kurtosis(rets, fisher=True, bias=False))
    _, kurt_p = sstats.normaltest(rets)  # tests normality overall (skew+kurtosis);
                                          # used here as the kurtosis significance proxy

    ac1, ac1_p = _autocorr_lag1_with_pvalue(rets)
    abs_ac1, abs_ac1_p = _autocorr_lag1_with_pvalue(np.abs(rets))

    return StylizedFactsReport(
        n_returns=len(rets),
        excess_kurtosis=kurt,
        kurtosis_pvalue=float(kurt_p),
        return_autocorr_lag1=ac1,
        return_autocorr_pvalue=ac1_p,
        abs_return_autocorr_lag1=abs_ac1,
        abs_return_autocorr_pvalue=abs_ac1_p,
    )
