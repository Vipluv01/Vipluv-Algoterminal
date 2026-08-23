"""A minimal scalar Kalman filter for a time-varying hedge ratio between
two price series -- the same dynamic-hedge-ratio approach
icici_mean_reversion uses (in preference to a static OLS beta, which goes
stale as the real relationship between two stocks drifts over months of
data).

Model: prices_a[t] = beta[t] * prices_b[t] + noise, with beta itself
following a random walk (beta[t] = beta[t-1] + process_noise). This is the
standard formulation used in pairs-trading literature (e.g. Ernest Chan's
"Algorithmic Trading"), not a bespoke invention -- implemented directly
rather than pulling in a Kalman-filter library, since the scalar case here
is a handful of lines and pykalman/filterpy would be a heavy dependency for
that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class KalmanHedgeRatio:
    process_var: float = 1e-5   # how fast beta is allowed to drift -- small, since a
                                  # hedge ratio between two large-cap banks should be
                                  # slowly-varying, not noisy day to day
    obs_var: float = 1e-3        # measurement noise in the price relationship itself
    beta: float = field(default=1.0)
    p: float = field(default=1.0)  # estimation error variance

    def update(self, y: float, x: float) -> float:
        """One step: observe y (=prices_a[t]) and regressor x (=prices_b[t]),
        return the updated beta estimate."""
        beta_pred = self.beta
        p_pred = self.p + self.process_var

        innovation = y - x * beta_pred
        innovation_var = x * x * p_pred + self.obs_var
        gain = p_pred * x / innovation_var if innovation_var > 0 else 0.0

        self.beta = beta_pred + gain * innovation
        self.p = (1.0 - gain * x) * p_pred
        return self.beta


def hedge_ratio_series(prices_a: np.ndarray, prices_b: np.ndarray, **kwargs) -> np.ndarray:
    """Runs the filter across the whole series, returning beta[t] for every t."""
    kf = KalmanHedgeRatio(**kwargs)
    betas = np.empty(len(prices_a))
    for i in range(len(prices_a)):
        betas[i] = kf.update(prices_a[i], prices_b[i])
    return betas
