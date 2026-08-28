"""Option chain math: strike ladder, expiry calendar, IV smile, contract
key convention, and the deterministic synthetic OI/volume generator."""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.markets import MarketRegistry
from app.options import chain
from app.quant.black_scholes import bsm_price


# ---------------------------------------------------------------------------
# Expiry calendar
# ---------------------------------------------------------------------------

def test_next_weekly_expiry_is_this_thursday_when_today_is_a_thursday():
    thursday = date(2026, 9, 3)  # a real Thursday
    assert thursday.weekday() == 3
    assert chain._next_weekly_expiry(thursday) == thursday


def test_next_weekly_expiry_rolls_forward_to_the_nearest_thursday():
    monday = date(2026, 9, 7)
    assert chain._next_weekly_expiry(monday) == date(2026, 9, 10)


def test_next_monthly_expiry_is_the_last_thursday_of_the_month():
    # September 2026: last Thursday is the 24th.
    assert chain._last_thursday_of_month(2026, 9) == date(2026, 9, 24)


def test_next_monthly_expiry_rolls_to_next_month_once_this_months_has_passed():
    after_expiry = date(2026, 9, 25)
    assert chain._next_monthly_expiry(after_expiry) == chain._last_thursday_of_month(2026, 10)


def test_list_expiries_returns_weekly_then_monthly_distinct_dates():
    infos = chain.list_expiries(today=date(2026, 9, 7))
    kinds = [e.kind for e in infos]
    assert kinds[0] == "weekly"
    dates = [e.date for e in infos]
    assert len(dates) == len(set(dates))  # never duplicate a date under two kinds


def test_expiry_label_matches_the_spec_example_format():
    assert chain._expiry_label(date(2026, 9, 26)) == "26SEP"


# ---------------------------------------------------------------------------
# Time to expiry
# ---------------------------------------------------------------------------

def test_time_to_expiry_years_is_zero_days_gives_a_tiny_positive_floor():
    today = date(2026, 9, 26)
    T = chain.time_to_expiry_years(today.isoformat(), as_of=today)
    assert T > 0.0


def test_time_to_expiry_years_scales_with_real_calendar_days():
    today = date(2026, 9, 1)
    expiry = date(2026, 9, 26)  # 25 days out
    T = chain.time_to_expiry_years(expiry.isoformat(), as_of=today)
    assert T == pytest.approx(25 / 365.0)


# ---------------------------------------------------------------------------
# Strike ladder / step
# ---------------------------------------------------------------------------

def test_strike_step_is_proportional_to_spot_not_a_fixed_real_nse_interval():
    """This project's derived indices live at a wholly different price
    scale than real NIFTY/BANKNIFTY (see app/markets.py's own docstring on
    why they're an equal-weighted average, not a rebased index level) --
    a fixed 50pt/100pt NSE-real interval would be wildly oversized here."""
    small_step = chain.strike_step("NIFTY50", 1200.0)
    large_step = chain.strike_step("NIFTY50", 40000.0)
    assert small_step < large_step
    assert small_step > 0


def test_format_strike_drops_trailing_zero_for_whole_numbers():
    assert chain.format_strike(22000.0) == "22000"


# ---------------------------------------------------------------------------
# Contract key convention
# ---------------------------------------------------------------------------

def test_build_contract_key_matches_the_spec_example():
    key = chain.build_contract_key("BANKNIFTY", "2026-09-26", 52000.0, "CE")
    assert key == "BANKNIFTY26SEP52000CE"


def test_build_contract_key_is_deterministic_and_unique_per_strike():
    key_a = chain.build_contract_key("NIFTY50", "2026-09-26", 22000.0, "PE")
    key_b = chain.build_contract_key("NIFTY50", "2026-09-26", 22100.0, "PE")
    assert key_a != key_b
    assert chain.build_contract_key("NIFTY50", "2026-09-26", 22000.0, "PE") == key_a


# ---------------------------------------------------------------------------
# IV smile
# ---------------------------------------------------------------------------

def test_smile_iv_is_minimized_at_the_money():
    spot = 1250.0
    sigma0 = 0.20
    atm_iv = chain.smile_iv(spot, spot, sigma0)
    otm_iv = chain.smile_iv(spot * 1.1, spot, sigma0)
    assert atm_iv == pytest.approx(sigma0)
    assert otm_iv > atm_iv


def test_smile_iv_is_symmetric_in_log_moneyness():
    spot = 1250.0
    sigma0 = 0.20
    call_wing = chain.smile_iv(spot * 1.1, spot, sigma0)
    put_wing = chain.smile_iv(spot / 1.1, spot, sigma0)
    assert call_wing == pytest.approx(put_wing)


# ---------------------------------------------------------------------------
# Deterministic synthetic OI/volume
# ---------------------------------------------------------------------------

def test_synthetic_oi_and_volume_is_deterministic_across_calls():
    a = chain._synthetic_oi_and_volume("NIFTY5026SEP22000CE", 0)
    b = chain._synthetic_oi_and_volume("NIFTY5026SEP22000CE", 0)
    assert a == b


def test_synthetic_oi_and_volume_decays_away_from_atm():
    atm_oi, atm_volume = chain._synthetic_oi_and_volume("X", 0)
    far_oi, far_volume = chain._synthetic_oi_and_volume("X", 10)
    assert far_oi < atm_oi
    assert far_volume < atm_volume


# ---------------------------------------------------------------------------
# get_option_chain end-to-end
# ---------------------------------------------------------------------------

def test_get_option_chain_has_21_strikes_10_each_side_of_atm():
    registry = MarketRegistry(seed=0)
    try:
        result = chain.get_option_chain("NIFTY50", registry)
        assert len(result.rows) == 2 * chain.STRIKES_EACH_SIDE + 1
    finally:
        registry.close()


def test_get_option_chain_prices_match_bsm_directly():
    registry = MarketRegistry(seed=0)
    try:
        result = chain.get_option_chain("NIFTY50", registry)
        row = result.rows[len(result.rows) // 2]  # ATM-ish row
        T = chain.time_to_expiry_years(result.expiry)
        sigma0 = chain.live_atm_sigma("NIFTY50", registry)
        expected_call = bsm_price(
            result.spot, row.strike, T, chain.RISK_FREE_RATE, chain.smile_iv(row.strike, result.spot, sigma0), "CE",
        )
        assert row.call.theoretical_price == pytest.approx(expected_call)
    finally:
        registry.close()


def test_get_option_chain_rejects_unknown_expiry_gracefully_by_pricing_it_anyway():
    registry = MarketRegistry(seed=0)
    try:
        far_future = "2030-01-03"  # a real Thursday, but not one of the two live expiries
        result = chain.get_option_chain("NIFTY50", registry, expiry=far_future)
        assert result.expiry == far_future
        assert math.isfinite(result.rows[0].call.theoretical_price)
    finally:
        registry.close()


def test_get_option_chain_works_for_the_derived_banknifty_index():
    registry = MarketRegistry(seed=0)
    try:
        result = chain.get_option_chain("BANKNIFTY", registry)
        assert result.underlying == "BANKNIFTY"
        assert result.spot > 0
        assert len(result.rows) == 2 * chain.STRIKES_EACH_SIDE + 1
    finally:
        registry.close()
