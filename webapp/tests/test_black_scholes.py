import pytest

from app.quant.black_scholes import bsm_greeks, bsm_price, implied_volatility


# ---------------------------------------------------------------------------
# Textbook values
# ---------------------------------------------------------------------------

def test_matches_hulls_published_textbook_example():
    """S=42, K=40, T=0.5, r=0.10, sigma=0.20 -- the standard worked example
    in Hull's "Options, Futures, and Other Derivatives" (used across
    multiple editions), published call=$4.76, put=$0.81. Checked to the 2
    decimal places the textbook itself publishes, not more -- the book
    doesn't give enough digits to check tighter than that."""
    S, K, T, r, sigma = 42.0, 40.0, 0.5, 0.10, 0.20
    call = bsm_price(S, K, T, r, sigma, "CE")
    put = bsm_price(S, K, T, r, sigma, "PE")
    assert call == pytest.approx(4.76, abs=0.01)
    assert put == pytest.approx(0.81, abs=0.01)


def test_atm_call_and_put_are_equal_when_r_is_zero():
    """A textbook identity: with r=0, ATM call and put prices are exactly
    equal (put-call parity collapses to C=P when S=K and the discount
    factor is 1)."""
    S = K = 100.0
    call = bsm_price(S, K, 1.0, 0.0, 0.25, "CE")
    put = bsm_price(S, K, 1.0, 0.0, 0.25, "PE")
    assert call == pytest.approx(put, abs=1e-10)


# ---------------------------------------------------------------------------
# Put-call parity: C - P = S - K*exp(-rT)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("S,K,T,r,sigma", [
    (42.0, 40.0, 0.5, 0.10, 0.20),
    (100.0, 100.0, 1.0, 0.05, 0.30),
    (100.0, 120.0, 0.25, 0.03, 0.15),  # OTM call / ITM put
    (100.0, 80.0, 2.0, 0.07, 0.40),    # ITM call / OTM put, long-dated
    (50.0, 50.0, 0.01, 0.01, 0.10),    # very short-dated
])
def test_put_call_parity_holds_to_high_precision(S, K, T, r, sigma):
    import math
    call = bsm_price(S, K, T, r, sigma, "CE")
    put = bsm_price(S, K, T, r, sigma, "PE")
    lhs = call - put
    rhs = S - K * math.exp(-r * T)
    assert lhs == pytest.approx(rhs, abs=1e-8)


# ---------------------------------------------------------------------------
# Greeks
# ---------------------------------------------------------------------------

def test_call_delta_is_between_zero_and_one():
    g = bsm_greeks(100.0, 100.0, 1.0, 0.05, 0.25, "CE")
    assert 0.0 < g.delta < 1.0


def test_put_delta_is_between_minus_one_and_zero():
    g = bsm_greeks(100.0, 100.0, 1.0, 0.05, 0.25, "PE")
    assert -1.0 < g.delta < 0.0


def test_call_delta_minus_put_delta_equals_one():
    """d(C)/dS - d(P)/dS = 1 follows directly from differentiating the
    parity identity C - P = S - K*exp(-rT) with respect to S."""
    S, K, T, r, sigma = 100.0, 105.0, 0.75, 0.04, 0.22
    call_delta = bsm_greeks(S, K, T, r, sigma, "CE").delta
    put_delta = bsm_greeks(S, K, T, r, sigma, "PE").delta
    assert call_delta - put_delta == pytest.approx(1.0, abs=1e-10)


def test_gamma_and_vega_are_identical_for_call_and_put():
    """Both follow directly from the BSM formulas: gamma and vega have no
    call/put branch in their derivation (unlike delta, theta, rho)."""
    S, K, T, r, sigma = 100.0, 95.0, 0.5, 0.06, 0.30
    call = bsm_greeks(S, K, T, r, sigma, "CE")
    put = bsm_greeks(S, K, T, r, sigma, "PE")
    assert call.gamma == pytest.approx(put.gamma, abs=1e-12)
    assert call.vega == pytest.approx(put.vega, abs=1e-12)


def test_gamma_is_positive_and_peaks_near_the_money():
    """Gamma is always positive for a long option (convexity), and is
    higher ATM than deep ITM/OTM -- a basic shape check on the formula."""
    K, T, r, sigma = 100.0, 0.5, 0.05, 0.25
    gamma_atm = bsm_greeks(100.0, K, T, r, sigma, "CE").gamma
    gamma_otm = bsm_greeks(140.0, K, T, r, sigma, "CE").gamma
    gamma_itm = bsm_greeks(60.0, K, T, r, sigma, "CE").gamma
    assert gamma_atm > 0
    assert gamma_atm > gamma_otm
    assert gamma_atm > gamma_itm


def test_vega_is_positive_and_scaled_per_one_percent_vol():
    """vega here is per 1 PERCENTAGE POINT of sigma (0.20 -> 0.21), not per
    a full unit (0.20 -> 1.20) -- verified by CENTRAL finite-difference
    against bsm_price itself, independent of the closed-form Greeks
    formula. Central, not forward: vega itself has curvature (vomma), so a
    forward difference over a full 1pp step carries first-order truncation
    error from that curvature (measured directly: ~1.7e-4 off against a
    forward difference here) -- a central difference cancels that to
    second order and matches the closed form far more tightly."""
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.25
    vega = bsm_greeks(S, K, T, r, sigma, "CE").vega
    assert vega > 0

    price_minus = bsm_price(S, K, T, r, sigma - 0.005, "CE")
    price_plus = bsm_price(S, K, T, r, sigma + 0.005, "CE")
    central_diff_vega = price_plus - price_minus  # per 1pp (0.005+0.005=0.01 total move)
    assert vega == pytest.approx(central_diff_vega, abs=1e-5)


def test_theta_is_negative_for_a_long_call_with_positive_rate():
    """Standard case: time decay works against a long option holder."""
    g = bsm_greeks(100.0, 100.0, 0.5, 0.05, 0.25, "CE")
    assert g.theta < 0


def test_theta_matches_finite_difference_across_one_day():
    """theta is quoted per CALENDAR day -- verified against an actual
    finite difference in T of 1/365 year, independent of the closed-form
    theta formula."""
    S, K, T, r, sigma = 100.0, 100.0, 0.5, 0.05, 0.25
    theta = bsm_greeks(S, K, T, r, sigma, "CE").theta

    price_now = bsm_price(S, K, T, r, sigma, "CE")
    price_one_day_later = bsm_price(S, K, T - 1.0 / 365.0, r, sigma, "CE")
    finite_diff_theta = price_one_day_later - price_now
    assert theta == pytest.approx(finite_diff_theta, abs=1e-3)


def test_rho_is_positive_for_a_call_and_negative_for_a_put():
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.25
    assert bsm_greeks(S, K, T, r, sigma, "CE").rho > 0
    assert bsm_greeks(S, K, T, r, sigma, "PE").rho < 0


def test_delta_matches_finite_difference_in_spot():
    S, K, T, r, sigma = 100.0, 100.0, 1.0, 0.05, 0.25
    delta = bsm_greeks(S, K, T, r, sigma, "CE").delta
    bump = 0.01
    finite_diff = (bsm_price(S + bump, K, T, r, sigma, "CE") - bsm_price(S - bump, K, T, r, sigma, "CE")) / (2 * bump)
    assert delta == pytest.approx(finite_diff, abs=1e-4)


# ---------------------------------------------------------------------------
# Implied volatility
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("S,K,T,r,sigma,option_type", [
    (100.0, 100.0, 0.5, 0.05, 0.20, "CE"),   # ATM call
    (100.0, 100.0, 0.5, 0.05, 0.20, "PE"),   # ATM put
    (100.0, 110.0, 0.25, 0.03, 0.25, "CE"),  # OTM call
    (100.0, 90.0, 0.25, 0.03, 0.25, "CE"),   # ITM call
    (100.0, 90.0, 0.5, 0.05, 0.35, "PE"),    # ITM put
    (100.0, 100.0, 0.02, 0.01, 0.60, "CE"),  # short-dated, high vol
    (100.0, 100.0, 2.0, 0.06, 0.15, "PE"),   # long-dated, low vol
])
def test_implied_volatility_round_trips_through_price(S, K, T, r, sigma, option_type):
    """The actual acceptance criterion: re-pricing at the recovered IV
    must reproduce the market price that was inverted, for a range of
    moneyness/tenor/vol combinations a real option chain would present.

    Not tested: prices so far out-of-the-money that they round to
    numerically indistinguishable-from-zero regardless of sigma (e.g. a
    strike 50% out with days to expiry) -- that is a genuinely ill-posed
    inversion (the pricing function is too flat there to invert reliably
    at any tolerance), not a defect in the solver, and no root-finder
    fixes an unidentifiable inverse problem.
    """
    market_price = bsm_price(S, K, T, r, sigma, option_type)
    iv = implied_volatility(market_price, S, K, T, r, option_type)
    repriced = bsm_price(S, K, T, r, iv, option_type)
    assert repriced == pytest.approx(market_price, abs=1e-6)


def test_implied_volatility_recovers_the_true_sigma_for_well_conditioned_inputs():
    """Beyond re-pricing consistency: for an ordinary, liquid-like
    contract, the recovered IV should also be close to the true sigma
    that generated the price (a stronger claim than round-tripping alone,
    since round-tripping could in principle be satisfied by a sigma far
    from the true one if the pricing function weren't well-behaved there
    -- ATM, moderate tenor is exactly where it is well-behaved)."""
    S, K, T, r, true_sigma = 100.0, 100.0, 0.5, 0.05, 0.28
    market_price = bsm_price(S, K, T, r, true_sigma, "CE")
    iv = implied_volatility(market_price, S, K, T, r, "CE")
    assert iv == pytest.approx(true_sigma, abs=1e-4)


def test_implied_volatility_rejects_a_price_below_intrinsic_value():
    """A price below intrinsic value is not achievable by ANY sigma --
    arbitrage-violating input, must raise rather than return a nonsense
    (or negative) implied vol."""
    S, K, T, r = 120.0, 100.0, 0.5, 0.05  # intrinsic (call) = 20.0 minimum
    with pytest.raises(ValueError):
        implied_volatility(5.0, S, K, T, r, "CE")
