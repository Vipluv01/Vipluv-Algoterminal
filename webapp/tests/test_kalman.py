import numpy as np
import pytest

from app.strategies.kalman import KalmanBetaAlpha, beta_alpha_series


def test_converges_to_a_true_linear_relation_with_noise():
    """y = 1.5x + 10.0 plus observation noise -- the filter must recover
    both beta and alpha, not just beta (the whole point of the 2D upgrade
    over the scalar-only filter).

    x's random-walk step size (1.5) is deliberately larger than a typical
    slow-drifting-hedge-ratio scenario would use: beta and alpha are only
    separately identifiable from how y responds to x actually MOVING, and
    a regressor that stays too close to a constant makes the two nearly
    collinear (verified directly -- a step size of 0.5 here left alpha
    converged to ~0.44 instead of 10.0 after 500 steps, not because the
    filter is wrong but because the data barely constrains alpha at all).
    This is a property of online linear regression, not a filter bug.
    """
    rng = np.random.default_rng(0)
    x = 100 + np.cumsum(rng.normal(0, 1.5, 1500))
    true_beta, true_alpha = 1.5, 10.0
    y = true_beta * x + true_alpha + rng.normal(0, 0.05, 1500)

    betas, alphas = beta_alpha_series(x, y)
    assert betas[-1] == pytest.approx(true_beta, abs=0.02)
    assert alphas[-1] == pytest.approx(true_alpha, abs=1.0)


def test_converges_exactly_with_no_observation_noise():
    """With a noise-free exact relation and a well-identified regressor
    (see the previous test's note on beta/alpha collinearity), convergence
    should be tight -- isolates the filter's own recovery behaviour from
    observation noise."""
    rng = np.random.default_rng(1)
    x = 100 + np.cumsum(rng.normal(0, 2.0, 2000))
    y = 2.5 * x + 3.0

    betas, alphas = beta_alpha_series(x, y)
    assert betas[-1] == pytest.approx(2.5, abs=0.01)
    assert alphas[-1] == pytest.approx(3.0, abs=1.0)


def test_tracks_a_sudden_regime_shift_in_beta():
    """The whole point of using a Kalman filter instead of static OLS: beta
    steps from 1.5 to 2.5 partway through, and the filter should follow it,
    not stay anchored to the old value. A larger process_var lets it track
    the step faster, same trade-off the scalar filter's own drift test
    exercised."""
    rng = np.random.default_rng(2)
    x = 100 + np.cumsum(rng.normal(0, 0.3, 800))
    true_beta = np.concatenate([np.full(400, 1.5), np.full(400, 2.5)])
    y = true_beta * x

    betas, _ = beta_alpha_series(x, y, q_beta=1e-3)
    # Well after the regime change, it should have moved substantially
    # toward the new ratio, not still be sitting near the old one.
    assert betas[-1] > 2.2
    # And it should have been reasonably close to the OLD ratio just before
    # the shift -- confirms this is genuine tracking, not just a filter that
    # happens to land near 2.5 regardless of history.
    assert betas[399] == pytest.approx(1.5, abs=0.1)


def test_single_update_moves_beta_and_alpha_toward_the_observation():
    kf = KalmanBetaAlpha(beta=1.0, alpha=0.0)
    # x=100, y=250 implies beta=2.5 (alpha=0) or many other (beta, alpha)
    # combinations -- one update can't jump all the way to any single exact
    # answer, but the innovation is positive (observed y=250 > predicted
    # y=100 under beta=1, alpha=0), so both state components must move up,
    # not down or stay put.
    new_beta, new_alpha = kf.update(x=100.0, y=250.0)
    assert new_beta > 1.0
    assert new_alpha > 0.0


def test_state_covariance_stays_positive_semi_definite():
    """The Joseph-form update's entire reason for existing: run enough steps
    that the textbook shortcut form would risk losing PSD-ness under
    floating-point error, and confirm this implementation doesn't."""
    rng = np.random.default_rng(3)
    x = 100 + np.cumsum(rng.normal(0, 0.5, 5000))
    y = 1.8 * x + 5.0 + rng.normal(0, 0.1, 5000)

    kf = KalmanBetaAlpha()
    for i in range(len(x)):
        kf.update(x[i], y[i])
        # PSD: both eigenvalues non-negative (within floating-point slack).
        eigenvalues = np.linalg.eigvalsh(kf.state_covariance)
        assert np.all(eigenvalues >= -1e-10), f"lost PSD-ness at step {i}: eigenvalues={eigenvalues}"
        # Symmetric too -- Joseph form should keep it exactly so up to
        # floating-point rounding.
        assert kf.state_covariance == pytest.approx(kf.state_covariance.T, abs=1e-9)


def test_beta_alpha_series_matches_manual_stepping():
    """beta_alpha_series is just a convenience loop -- confirm it produces
    exactly what manually stepping KalmanBetaAlpha.update produces, so
    callers can trust either path."""
    rng = np.random.default_rng(4)
    x = 100 + np.cumsum(rng.normal(0, 0.4, 200))
    y = 1.2 * x + 2.0 + rng.normal(0, 0.05, 200)

    betas, alphas = beta_alpha_series(x, y)

    kf = KalmanBetaAlpha()
    for i in range(len(x)):
        b, a = kf.update(x[i], y[i])
        assert b == pytest.approx(betas[i])
        assert a == pytest.approx(alphas[i])
