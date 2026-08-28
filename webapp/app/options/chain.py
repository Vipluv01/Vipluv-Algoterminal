"""The synthetic option chain: strike ladder, weekly/monthly expiries, a
volatility smile, BSM theoretical pricing, and deterministic synthetic
open-interest/volume -- everything a chain UI needs to render, computed
fresh from the underlying's current price rather than stored anywhere.

There is no real options market here (see app/options/execution.py's own
docstring on why fills are model-priced) -- this module is the "what would
this contract be worth right now" oracle every other options module
(execution, Greeks, the 4 options strategies) calls into, so the pricing
model lives in exactly one place.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import numpy as np

from app.markets import MarketRegistry
from app.quant.black_scholes import OptionType, bsm_price

# Annualized, continuously-compounded -- a fixed illustrative NSE-ish
# short-term rate, not a live rate feed (nothing in this project has one).
RISK_FREE_RATE = 0.065

# sigma(K, T) = sigma0 + IV_GAMMA * ln(K/S)^2 -- ATM vol plus a symmetric
# skew that widens vol for strikes further from spot in EITHER direction (a
# "smile", not a one-sided "skew"). IV_GAMMA (the smile's SHAPE) is a fixed,
# illustrative constant -- there's no real options market to fit it
# against. sigma0 (the smile's LEVEL) is deliberately NOT a fixed constant
# -- see realized_vol_annualized below for why a hardcoded ATM vol was a
# real, serious bug, not just an approximation.
IV_GAMMA = 0.35

# A rolling window of bars/ticks -- long enough to average out single-tick
# noise, short enough to track a genuine regime change; not fit to
# anything, just a reasonable middle ground.
REALIZED_VOL_WINDOW = 100
# Sanity bounds on the calibrated ATM vol -- see realized_vol_annualized's
# own docstring for the two DIFFERENT floors this module uses and why they
# have to differ (BACKTEST_VOL_FLOOR vs LIVE_VOL_FLOOR, below). The
# ceiling is a pure numerical safety net in both domains, guarding only
# against a pathological outlier window, never expected to bind.
REALIZED_VOL_CEILING = 3.0

# BACKTEST domain: a pure NUMERICAL safety net, nothing more. A window
# that's been perfectly flat (a real possibility for this simulation --
# see realized_vol_annualized's own docstring on just how little this
# market actually moves) would otherwise calibrate to sigma=0, which
# bsm_price/bsm_greeks reject outright (division by sigma*sqrt(T) in
# _d1_d2). This floor is deliberately as small as it can be while still
# keeping that math well-defined -- large enough ONLY for that, not large
# enough to substitute a plausible-sounding constant for what the
# underlying is actually doing. A bigger "looks like a real index" floor
# here would reintroduce a smaller version of the exact pricing-vs-reality
# mismatch this whole calibration exists to remove: the backtest's job is
# to report the underlying's TRUE (if genuinely quiet) risk, not a
# comfortable-looking one.
BACKTEST_VOL_FLOOR = 1e-4

# LIVE domain: a real, independently-defensible floor -- NOT the same
# value as BACKTEST_VOL_FLOOR, and not for the same reason. Two problems
# showed up using the backtest's tiny numerical floor here too: (1) no
# real listed options market is ever quoted near 0.01% implied vol --
# even the quietest real underlyings carry SOME priced-in uncertainty,
# so a floor this low isn't more "honest," it's just unrealistic in the
# other direction; and (2) at that floor, d1 = ln(K/S)/(sigma*sqrt(T))
# blows up for ANY strike even slightly off the exact spot, saturating
# norm.cdf(d1) to EXACTLY 0.0 or 1.0 -- found directly via a failing
# Greeks test: a real, ATM-ADJACENT (not exactly-at-spot) long call
# position reported delta=0.0, gamma=0.0, vega=0.0 across the board, a
# numerically degenerate result that defeats the entire Greeks/chain
# display feature, not an honest "this market is quiet" answer. 5%
# reflects real markets' own practical floor (even the least volatile
# real listed underlyings rarely price below this) and keeps BSM's math
# well-behaved across a realistic strike ladder -- used for the live
# option chain, execution marks, and portfolio Greeks, never for backtest
# scoring.
LIVE_VOL_FLOOR = 0.05

# One live tick == roughly one real second (app/main.py's MARKET_TICK_SECONDS)
# -- used ONLY to annualize a realized-vol estimate computed from LIVE
# price history, so the vol fed into BSM pricing is expressed in the same
# calendar terms as time_to_expiry_years (real days). This is a genuinely
# different bars-per-year scale than the backtest's own
# app.backtest.adapters.BACKTEST_BARS_PER_YEAR -- the two domains tick at
# different real-world rates, and each vol estimate must be annualized
# consistently with the SAME clock its own time-to-expiry is computed on,
# not the other domain's.
LIVE_BARS_PER_YEAR = 365.0 * 24.0 * 3600.0


def realized_vol_annualized(
    prices: np.ndarray, bars_per_year: float, window: int = REALIZED_VOL_WINDOW,
    floor: float = BACKTEST_VOL_FLOOR, ceiling: float = REALIZED_VOL_CEILING,
) -> float:
    """Annualized volatility estimated directly from `prices`' own recent
    log returns -- THE fix for a real, serious bug: an earlier version of
    this module priced every option off a fixed IV_SIGMA0=0.18 (18%
    annualized) regardless of what the underlying was actually doing.
    Measured directly: this simulation's own per-bar realized vol
    annualizes to well under 1% under the backtest's bars-per-year
    convention -- a ~200x mismatch against the old fixed 18% constant,
    which made selling option premium a near risk-free win by
    construction (a short-dated, ~8%-OTM strangle expires worthless
    almost every time when priced off 18% vol against an underlying that
    barely moves at all) and buying premium a guaranteed bleed. Calibrating
    sigma0 to the SAME underlying's own measured behavior, annualized
    self-consistently with whichever bars-per-year clock computed the
    option's time-to-expiry, removes that mismatch: an option is priced
    against what its own underlying actually does, not an arbitrary
    unrelated number.

    `floor` defaults to BACKTEST_VOL_FLOOR (a pure numerical safety net);
    live_atm_sigma below passes LIVE_VOL_FLOOR instead -- see that
    constant's own comment for why the two domains need different floors.
    """
    window_prices = prices[-(window + 1):]
    if len(window_prices) < 2:
        return floor
    log_returns = np.diff(np.log(window_prices))
    per_bar_vol = float(log_returns.std())
    annualized = per_bar_vol * (bars_per_year ** 0.5)
    return float(np.clip(annualized, floor, ceiling))


def live_atm_sigma(underlying: str, registry: MarketRegistry, window: int = REALIZED_VOL_WINDOW) -> float:
    """The ATM vol every LIVE pricing call site (get_option_chain,
    app/options/execution.py, app/options/greeks.py, app/options/
    live_dispatch.py) should use -- calibrated from this underlying's own
    real price_history (registry.price_history_for handles both a real
    instrument and a derived index uniformly), annualized via
    LIVE_BARS_PER_YEAR, floored at LIVE_VOL_FLOOR (not the backtest's
    tiny numerical floor -- see that constant's own comment). The
    backtest domain uses its own equivalent (OptionsBacktestAdapter.
    _atm_sigma) rather than this function, since a backtest bar and a
    live tick are not the same unit of time.
    """
    return realized_vol_annualized(
        registry.price_history_for(underlying), LIVE_BARS_PER_YEAR, window=window, floor=LIVE_VOL_FLOOR,
    )

STRIKES_EACH_SIDE = 10  # 10 ITM + ATM + 10 OTM = 21 strikes per expiry, per spec

# Real NSE index levels justify a fixed 50pt (NIFTY)/100pt (BANKNIFTY)
# strike interval -- but this project's derived indices (app/markets.py's
# DERIVED_INDICES) are an equal-weighted AVERAGE of raw constituent share
# prices (~800-4000), not a rebased ~20000-50000 index level, so those
# real-world fixed intervals would put a "few strikes OTM" option's strike
# absurdly far from spot in RELATIVE terms. Every underlying -- index or
# equity alike -- instead gets a step proportional to its own spot,
# rounded to a clean multiple of 5.
_DAYS_PER_YEAR = 365.0

ExpiryKind = Literal["weekly", "monthly"]


def strike_step(underlying: str, spot: float) -> float:
    step = round(spot * 0.02 / 5.0) * 5.0
    return step if step > 0 else 5.0


def _next_weekly_expiry(today: date) -> date:
    """NSE's weekly expiry day is Thursday. "Next" includes today itself
    if today IS a Thursday -- a chain queried on expiry day still has a
    real (same-day) weekly contract until it actually expires."""
    days_until_thursday = (3 - today.weekday()) % 7  # Monday=0 ... Thursday=3
    return today + timedelta(days=days_until_thursday)


def _last_thursday_of_month(year: int, month: int) -> date:
    if month == 12:
        first_of_next_month = date(year + 1, 1, 1)
    else:
        first_of_next_month = date(year, month + 1, 1)
    last_day = first_of_next_month - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - 3) % 7)


def _next_monthly_expiry(today: date) -> date:
    """The last Thursday of the current month, or of next month if this
    month's has already passed."""
    candidate = _last_thursday_of_month(today.year, today.month)
    if candidate >= today:
        return candidate
    if today.month == 12:
        return _last_thursday_of_month(today.year + 1, 1)
    return _last_thursday_of_month(today.year, today.month + 1)


@dataclass(frozen=True)
class ExpiryInfo:
    date: str    # ISO, unambiguous -- what time-to-expiry math uses
    label: str   # compact "26SEP" NSE-style form -- what the contract key uses
    kind: ExpiryKind


def _expiry_label(d: date) -> str:
    return f"{d.day:02d}{d.strftime('%b').upper()}"


def list_expiries(today: date | None = None) -> list[ExpiryInfo]:
    """The weekly and monthly expiries currently available to trade --
    same two choices for every underlying (NSE's calendar isn't per-
    symbol), computed from the real wall-clock date."""
    today = today or date.today()
    weekly = _next_weekly_expiry(today)
    monthly = _next_monthly_expiry(today)
    infos = [ExpiryInfo(date=weekly.isoformat(), label=_expiry_label(weekly), kind="weekly")]
    if monthly != weekly:
        infos.append(ExpiryInfo(date=monthly.isoformat(), label=_expiry_label(monthly), kind="monthly"))
    return infos


def time_to_expiry_years(expiry_iso: str, as_of: date | None = None) -> float:
    """Years between `as_of` (default: today) and the expiry date, floored
    to a small positive epsilon rather than 0 -- bsm_price/bsm_greeks both
    raise on T<=0 (see app/quant/black_scholes.py), and an option priced
    ON its own expiry day still needs a valid (if tiny) T to price against,
    not a crash."""
    as_of = as_of or date.today()
    expiry_date = date.fromisoformat(expiry_iso)
    days = (expiry_date - as_of).days
    return max(days, 0) / _DAYS_PER_YEAR or (1.0 / _DAYS_PER_YEAR / 24)  # ~1 hour floor


def smile_iv(strike: float, spot: float, sigma0: float, gamma: float = IV_GAMMA) -> float:
    """sigma0 has NO default -- every caller must explicitly supply a
    real, calibrated ATM vol (live_atm_sigma for live trading,
    OptionsBacktestAdapter._atm_sigma for backtesting), never a bare
    fixed constant. See realized_vol_annualized's own docstring for why a
    fixed default here was a real, serious pricing bug."""
    import math
    return sigma0 + gamma * (math.log(strike / spot) ** 2)


def format_strike(strike: float) -> str:
    return str(int(strike)) if strike == int(strike) else str(strike)


def build_contract_key(underlying: str, expiry_iso: str, strike: float, option_type: OptionType) -> str:
    """underlying + compact expiry label + strike + CE/PE, e.g.
    "BANKNIFTY26SEP52000CE" -- the exact convention this phase's spec
    gives, and the string stored directly in orders.symbol (see
    app/models/trading.py's Order.instrument_type docstring)."""
    label = _expiry_label(date.fromisoformat(expiry_iso))
    return f"{underlying}{label}{format_strike(strike)}{option_type}".upper()


def _deterministic_rng_seed(*parts: object) -> int:
    key = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def _synthetic_oi_and_volume(contract_key: str, distance_from_atm_steps: int) -> tuple[int, int]:
    """Deterministic (same contract_key -> same numbers, every call,
    forever) and reproducible (no hidden mutable state) -- both OI and
    volume decay with distance from ATM, matching how a real chain's
    liquidity actually concentrates near the money, plus a small
    per-contract jitter seeded off the contract key itself so two
    adjacent strikes don't look suspiciously identical.
    """
    import numpy as np
    rng = np.random.default_rng(_deterministic_rng_seed(contract_key))
    decay = 1.0 / (1.0 + 0.35 * abs(distance_from_atm_steps))
    base_oi = 50_000 * decay
    base_volume = 8_000 * decay
    jitter = rng.uniform(0.85, 1.15)
    return int(base_oi * jitter), int(base_volume * jitter)


@dataclass(frozen=True)
class OptionQuote:
    contract_key: str
    strike: float
    option_type: OptionType
    theoretical_price: float
    iv: float
    open_interest: int
    volume: int


@dataclass(frozen=True)
class OptionChainRow:
    strike: float
    call: OptionQuote
    put: OptionQuote


@dataclass(frozen=True)
class OptionChain:
    underlying: str
    spot: float
    expiry: str        # ISO date
    expiry_label: str  # "26SEP"
    rows: list[OptionChainRow]


def _quote(underlying: str, spot: float, expiry_iso: str, strike: float, option_type: OptionType,
           T: float, distance_from_atm_steps: int, sigma0: float) -> OptionQuote:
    contract_key = build_contract_key(underlying, expiry_iso, strike, option_type)
    iv = smile_iv(strike, spot, sigma0)
    price = bsm_price(spot, strike, T, RISK_FREE_RATE, iv, option_type)
    oi, volume = _synthetic_oi_and_volume(contract_key, distance_from_atm_steps)
    return OptionQuote(contract_key=contract_key, strike=strike, option_type=option_type,
                        theoretical_price=price, iv=iv, open_interest=oi, volume=volume)


def get_option_chain(underlying: str, registry: MarketRegistry, expiry: str | None = None) -> OptionChain:
    """expiry, when given, is an ISO date from list_expiries() -- defaults
    to the nearest weekly expiry."""
    spot = registry.current_prices()[underlying]
    expiry_info = list_expiries()[0] if expiry is None else next(
        (e for e in list_expiries() if e.date == expiry), None,
    )
    if expiry_info is None:
        # An expiry the caller asked for isn't one of the two currently
        # live choices -- still price it (T is just a date difference, and
        # a strategy/backtest may legitimately want an arbitrary future
        # date), just without a matching weekly/monthly label.
        expiry_info = ExpiryInfo(date=expiry, label=_expiry_label(date.fromisoformat(expiry)), kind="weekly")

    step = strike_step(underlying, spot)
    atm_strike = round(spot / step) * step
    T = time_to_expiry_years(expiry_info.date)
    sigma0 = live_atm_sigma(underlying, registry)

    rows = []
    for offset in range(-STRIKES_EACH_SIDE, STRIKES_EACH_SIDE + 1):
        strike = atm_strike + offset * step
        if strike <= 0:
            continue
        call = _quote(underlying, spot, expiry_info.date, strike, "CE", T, offset, sigma0)
        put = _quote(underlying, spot, expiry_info.date, strike, "PE", T, offset, sigma0)
        rows.append(OptionChainRow(strike=strike, call=call, put=put))

    return OptionChain(underlying=underlying, spot=spot, expiry=expiry_info.date,
                        expiry_label=expiry_info.label, rows=rows)
