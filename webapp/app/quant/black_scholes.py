"""Black-Scholes-Merton pricing, Greeks, and implied volatility.

Pure pricing math, no order routing or position tracking here -- this is
the model Phase 5's synthetic options layer prices fills against (at
theoretical price plus an explicit modeled spread, since there is no real
matched order book for an option the way there is for an equity through
bourse's Go engine). Kept here as a standalone module specifically so it
has nothing to do with execution: a caller wanting "what should this
option be worth" should never need to touch anything about how a fill
happens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

OptionType = Literal["CE", "PE"]  # NSE convention: CE = call, PE = put

DAYS_PER_YEAR = 365.0  # theta is quoted per CALENDAR day, not trading day --
                         # matches how time-to-expiry T is conventionally
                         # annualized in the first place (calendar time, not
                         # a 252-trading-day convention), so the two stay
                         # consistent with each other.


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    if S <= 0 or K <= 0:
        raise ValueError(f"S and K must be positive, got S={S}, K={K}")
    if T <= 0:
        raise ValueError(f"T must be positive (use intrinsic value directly at expiry), got T={T}")
    if sigma <= 0:
        raise ValueError(f"sigma must be positive, got sigma={sigma}")
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bsm_price(S: float, K: float, T: float, r: float, sigma: float, option_type: OptionType) -> float:
    """European option theoretical price. S=spot, K=strike, T=time to
    expiry in years, r=risk-free rate (annualized, continuously
    compounded), sigma=annualized volatility."""
    # Imported here, not at module top level, so a process that never
    # actually prices an option (e.g. one still running its Postgres
    # migrations at startup) never pays scipy's real import weight for it
    # -- see the Render memory investigation this came out of. Cheap after
    # the first call: once scipy.stats is in sys.modules, this is just a
    # dict lookup, not a re-import.
    from scipy.stats import norm
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    disc_K = K * math.exp(-r * T)
    if option_type == "CE":
        return S * norm.cdf(d1) - disc_K * norm.cdf(d2)
    return disc_K * norm.cdf(-d2) - S * norm.cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per CALENDAR day
    vega: float   # per 1% (0.01) absolute move in sigma
    rho: float    # per 1% (0.01) absolute move in r


def bsm_greeks(S: float, K: float, T: float, r: float, sigma: float, option_type: OptionType) -> Greeks:
    """First-order sensitivities of bsm_price to each input.

    theta is annualized internally then divided by DAYS_PER_YEAR to get a
    per-calendar-day figure -- the sign convention is the standard one
    (theta is normally negative for a long option: value decays as T
    shrinks, all else equal). vega and rho are scaled to a 1 PERCENTAGE
    POINT move (i.e. sigma: 0.20 -> 0.21, or r: 0.05 -> 0.06), matching how
    a trading desk actually reads these -- the raw per-unit (100
    percentage point) derivative is not a number anyone reasons about
    directly.
    """
    from scipy.stats import norm  # see bsm_price's own comment on why this is lazy
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    disc_K = K * math.exp(-r * T)
    pdf_d1 = norm.pdf(d1)
    sqrt_T = math.sqrt(T)

    gamma = pdf_d1 / (S * sigma * sqrt_T)  # same for call and put
    vega_per_unit = S * pdf_d1 * sqrt_T    # same for call and put, per 1.0 (100pp) vol move
    vega = vega_per_unit / 100.0

    if option_type == "CE":
        delta = norm.cdf(d1)
        theta_per_year = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T) - r * disc_K * norm.cdf(d2)
        )
        rho_per_unit = K * T * math.exp(-r * T) * norm.cdf(d2)
    else:
        delta = norm.cdf(d1) - 1.0  # == -norm.cdf(-d1)
        theta_per_year = (
            -(S * pdf_d1 * sigma) / (2 * sqrt_T) + r * disc_K * norm.cdf(-d2)
        )
        rho_per_unit = -K * T * math.exp(-r * T) * norm.cdf(-d2)

    return Greeks(
        delta=float(delta),
        gamma=float(gamma),
        theta=float(theta_per_year / DAYS_PER_YEAR),
        vega=float(vega),
        rho=float(rho_per_unit / 100.0),
    )


IV_MAX_ITERATIONS = 100
IV_TOLERANCE = 1e-8
IV_SIGMA_BOUNDS = (1e-6, 5.0)  # 0.0001% to 500% annualized vol -- generous
                                 # enough to bracket any real quote; bisection
                                 # only ever needs this as a last resort.


def implied_volatility(
    market_price: float, S: float, K: float, T: float, r: float, option_type: OptionType,
) -> float:
    """Solves bsm_price(..., sigma=result) == market_price for sigma.

    Newton-Raphson using vega as the derivative, with a BISECTION fallback
    -- not merely a lower iteration cap. Newton's step can overshoot into
    a non-positive or absurdly large sigma when vega is small (deep ITM/OTM,
    or short T), at which point _d1_d2 raises rather than returning a
    numerically-garbage answer; bisection is provably convergent on
    [IV_SIGMA_BOUNDS] as long as the market price is arbitrage-free (that
    interval brackets a sign change), so it is what actually GUARANTEES
    convergence rather than just extending the search.
    """
    intrinsic = max(0.0, (S - K) if option_type == "CE" else (K - S))
    if market_price < intrinsic - 1e-9:
        raise ValueError(
            f"market_price {market_price} is below intrinsic value {intrinsic} -- not a valid option price"
        )

    sigma = 0.20  # a reasonable universal starting guess
    for _ in range(IV_MAX_ITERATIONS):
        try:
            price = bsm_price(S, K, T, r, sigma, option_type)
            vega_per_unit = bsm_greeks(S, K, T, r, sigma, option_type).vega * 100.0
        except ValueError:
            break
        diff = price - market_price
        if abs(diff) < IV_TOLERANCE:
            return sigma
        if vega_per_unit < 1e-10:
            break
        sigma -= diff / vega_per_unit
        if sigma <= IV_SIGMA_BOUNDS[0] or sigma >= IV_SIGMA_BOUNDS[1]:
            break
    else:
        return sigma  # loop exhausted without breaking early -- last value is our best estimate

    return _implied_volatility_bisection(market_price, S, K, T, r, option_type)


def _implied_volatility_bisection(
    market_price: float, S: float, K: float, T: float, r: float, option_type: OptionType,
) -> float:
    lo, hi = IV_SIGMA_BOUNDS
    price_lo = bsm_price(S, K, T, r, lo, option_type) - market_price
    price_hi = bsm_price(S, K, T, r, hi, option_type) - market_price
    if price_lo * price_hi > 0:
        raise ValueError(
            f"market_price {market_price} is not bracketed by sigma in {IV_SIGMA_BOUNDS} "
            "-- likely an arbitrage-violating input (price outside any achievable BSM range)"
        )

    for _ in range(IV_MAX_ITERATIONS):
        mid = (lo + hi) / 2.0
        price_mid = bsm_price(S, K, T, r, mid, option_type) - market_price
        if abs(price_mid) < IV_TOLERANCE:
            return mid
        if price_lo * price_mid < 0:
            hi = mid
        else:
            lo, price_lo = mid, price_mid
    return (lo + hi) / 2.0
