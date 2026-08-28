"""A 2D Kalman filter for a time-varying hedge ratio AND intercept between
two price series -- the dynamic-hedge-ratio approach icici_mean_reversion
uses (in preference to a static OLS beta, which goes stale as the real
relationship between two stocks drifts over months of data), extended to
also track the intercept rather than assuming the relationship passes
through the origin.

Model: prices_a[t] = beta[t] * prices_b[t] + alpha[t] + noise, with the
state theta = [beta, alpha] following a random walk (theta[t] =
theta[t-1] + process_noise). Still the standard formulation from
pairs-trading literature (e.g. Ernest Chan's "Algorithmic Trading"), just
the two-parameter version -- implemented directly rather than pulling in a
Kalman-filter library, since the 2D case here is still a handful of lines
and pykalman/filterpy would be a heavy dependency for that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class KalmanBetaAlpha:
    """State theta_t = [beta_t, alpha_t]^T.

    Transition:  theta_t = theta_{t-1} + w_t,  w_t ~ N(0, Q),  Q = diag(q_beta, q_alpha)
    Observation: y_t = [x_t, 1] . theta_t + eps_t,  eps_t ~ N(0, R)

    q_beta defaults smaller than q_alpha (1e-5 vs 1e-4): a hedge ratio
    between two large-cap banks should be slowly-varying, day to day: the
    intercept absorbs any slower-moving relative drift between the two
    price levels (dividends, index rebalancing, differing corporate
    actions) and is allowed to move a bit faster.

    The covariance update uses the JOSEPH FORM,
        P_t = (I - K_t H_t) P_pred (I - K_t H_t)^T + K_t R K_t^T
    not the textbook shortcut P_t = (I - K_t H_t) P_pred. The shortcut is
    only exact under perfect arithmetic; under floating-point error it can
    lose positive-semi-definiteness, which is exactly the numerical
    failure mode a filter run over thousands of live ticks needs to not
    hit silently. This is the actual fix for filter stability -- NOT a
    clamp on beta. A clamped estimate would keep emitting hedge ratios
    that look plausible after the filter has already lost track, and this
    filter's beta is used to size a real order leg (see
    pairs_cointegration.py's _leg_b_qty) -- a silently-wrong hedge ratio
    is worse than a visibly-broken one. Divergence should be surfaced
    through the caller's own cointegration/warm-up gates, not hidden here.
    """

    q_beta: float = 1e-5
    q_alpha: float = 1e-4
    obs_var: float = 1e-3
    beta: float = field(default=1.0)
    alpha: float = field(default=0.0)
    state_covariance: np.ndarray = field(default_factory=lambda: np.eye(2))

    def update(self, x: float, y: float) -> tuple[float, float]:
        """One step: observe regressor x (=prices_b[t]) and dependent
        y (=prices_a[t]); returns the updated (beta, alpha)."""
        theta_pred = np.array([self.beta, self.alpha], dtype=float)
        Q = np.diag([self.q_beta, self.q_alpha])
        P_pred = self.state_covariance + Q

        H = np.array([x, 1.0])
        innovation = y - H @ theta_pred
        innovation_var = H @ P_pred @ H.T + self.obs_var
        K = (P_pred @ H) / innovation_var if innovation_var > 0 else np.zeros(2)

        theta_new = theta_pred + K * innovation

        I = np.eye(2)
        I_minus_KH = I - np.outer(K, H)
        self.state_covariance = (
            I_minus_KH @ P_pred @ I_minus_KH.T + np.outer(K, K) * self.obs_var
        )

        self.beta, self.alpha = float(theta_new[0]), float(theta_new[1])
        return self.beta, self.alpha


def beta_alpha_series(x: np.ndarray, y: np.ndarray, **kwargs) -> tuple[np.ndarray, np.ndarray]:
    """Runs the filter across the whole series, returning (beta[t], alpha[t])
    for every t. x is the regressor series (prices_b), y is the dependent
    series (prices_a) -- same argument order as KalmanBetaAlpha.update."""
    kf = KalmanBetaAlpha(**kwargs)
    betas = np.empty(len(x))
    alphas = np.empty(len(x))
    for i in range(len(x)):
        betas[i], alphas[i] = kf.update(x[i], y[i])
    return betas, alphas
