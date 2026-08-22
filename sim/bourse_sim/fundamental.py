"""The latent fundamental value: a price process nobody in the market can see
directly, only estimate.

This is the piece the original design (GBM setting the traded price directly)
didn't have, and its absence is what broke everything downstream in that
design: if the traded price literally IS the exogenous process, order flow
cannot move it, so the matching engine is decorative and any "trading
strategy" backtested against it is just curve-fitting to a random walk.

Here the fundamental is a hidden ground truth. Informed traders get a noisy
signal of it and trade toward that signal; noise traders trade for
liquidity/random reasons unrelated to it; the market maker sees neither the
fundamental nor the noise -- only the order flow. The TRADED price is
whatever the matching engine produces from that flow. That is the entire
mechanism that makes "price emerges from the book" true rather than asserted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class FundamentalProcess:
    """Geometric Brownian motion in continuous value space (not ticks).

    GBM is the right tool HERE, unlike for the traded price: it's modelling
    the slow-moving true value of the asset (earnings, macro conditions),
    which is a reasonable martingale assumption. What's different from the
    original flawed design is that this value is never written to the book
    directly -- it only reaches the market filtered through noisy agent
    signals and their own trading decisions.
    """

    s0: float
    mu: float = 0.0
    sigma: float = 0.20  # annualized-style vol; scaled by dt per step
    dt: float = 1.0 / (252 * 6.5 * 3600)  # one simulated second, ~trading-year units
    seed: int = 0

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self._value = self.s0

    @property
    def value(self) -> float:
        return self._value

    def step(self) -> float:
        z = self._rng.normal()
        drift = (self.mu - 0.5 * self.sigma**2) * self.dt
        diffusion = self.sigma * np.sqrt(self.dt) * z
        self._value *= np.exp(drift + diffusion)
        return self._value

    def noisy_signal(self, noise_sigma: float, *, rng: np.random.Generator) -> float:
        """What an informed trader actually observes: the true value plus
        their own private noise. Never the exact value -- if it were, every
        informed trader would agree perfectly and the model would produce
        unrealistically synchronized order flow."""
        return self._value * float(np.exp(rng.normal(0, noise_sigma)))
