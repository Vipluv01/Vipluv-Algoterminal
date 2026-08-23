import numpy as np
import pytest

from app.strategies.kalman import KalmanHedgeRatio, hedge_ratio_series


def test_converges_to_the_true_constant_ratio():
    """If a = 2.5 * b exactly (no noise), the filter must converge close
    to beta=2.5, not drift off to something else."""
    rng = np.random.default_rng(0)
    b = 100 + np.cumsum(rng.normal(0, 0.5, 500))
    a = 2.5 * b
    betas = hedge_ratio_series(a, b)
    assert betas[-1] == pytest.approx(2.5, abs=0.01)


def test_tracks_a_slow_drift_in_the_true_ratio():
    """The whole point of using a Kalman filter instead of static OLS: the
    true ratio changes partway through, and the filter should follow it,
    not stay anchored to the old value."""
    rng = np.random.default_rng(1)
    b = 100 + np.cumsum(rng.normal(0, 0.3, 800))
    true_beta = np.concatenate([np.full(400, 1.0), np.full(400, 1.5)])
    a = true_beta * b
    betas = hedge_ratio_series(a, b, process_var=1e-3)
    # Well after the regime change, it should have moved substantially
    # toward the new ratio, not still be sitting near the old one.
    assert betas[-1] > 1.3


def test_single_update_moves_beta_toward_the_observation():
    kf = KalmanHedgeRatio(beta=1.0, p=1.0)
    # Observation implies beta=2.0 exactly (y=200, x=100) -- one update
    # can't jump all the way there, but it must move in that direction.
    new_beta = kf.update(y=200.0, x=100.0)
    assert 1.0 < new_beta < 2.0
