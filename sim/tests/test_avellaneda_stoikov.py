"""Tests for the Avellaneda-Stoikov analytical market maker.

These check that the implementation actually has the theoretical properties
the model promises -- not just that the code runs. A formula transcribed
with a sign error still executes without crashing; it just makes the wrong
economic decision, and the point of this maker existing is to BE the
correct reference for the heuristic maker to be judged against.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

import numpy as np
import pytest

from avellaneda_stoikov import AvellanedaStoikovMaker, AvellanedaStoikovParams, estimate_k


def _maker(**kwargs) -> AvellanedaStoikovMaker:
    return AvellanedaStoikovMaker(trader_id=1, tick_size=0.01, **kwargs)


def test_reservation_price_equals_mid_at_zero_inventory():
    m = _maker()
    r = m.reservation_price_ticks(mid_ticks=10000, sigma_ticks=5.0, time_remaining=0.5)
    assert r == pytest.approx(10000.0), "flat inventory should quote around the raw mid, not a shifted price"


def test_reservation_price_shifts_down_when_long():
    """A market maker holding a long position should want to sell -- the
    model formalizes that as skewing the reservation price BELOW mid, which
    lowers both quotes and makes the ask more attractive to hit."""
    m = _maker()
    m.inventory = 100
    r = m.reservation_price_ticks(mid_ticks=10000, sigma_ticks=5.0, time_remaining=0.5)
    assert r < 10000.0, f"long inventory should push reservation price below mid, got {r}"


def test_reservation_price_shifts_up_when_short():
    m = _maker()
    m.inventory = -100
    r = m.reservation_price_ticks(mid_ticks=10000, sigma_ticks=5.0, time_remaining=0.5)
    assert r > 10000.0, f"short inventory should push reservation price above mid, got {r}"


def test_reservation_price_shift_scales_with_inventory_magnitude():
    """Twice the inventory should mean twice the skew (the formula is
    linear in q) -- catches an accidental sqrt, log, or clamp creeping in."""
    m1, m2 = _maker(), _maker()
    m1.inventory, m2.inventory = 50, 100
    mid, sigma, t = 10000, 5.0, 0.5
    shift1 = mid - m1.reservation_price_ticks(mid, sigma, t)
    shift2 = mid - m2.reservation_price_ticks(mid, sigma, t)
    assert shift2 == pytest.approx(2 * shift1, rel=1e-9)


def test_spread_widens_with_more_time_remaining():
    """More time left in the session means more opportunity for the price
    to move against an open position before it can be unwound -- the model
    should therefore quote a wider protective spread early in the session
    than right before it ends."""
    m = _maker()
    early = m.optimal_half_spread_ticks(sigma_ticks=5.0, time_remaining=1.0)
    late = m.optimal_half_spread_ticks(sigma_ticks=5.0, time_remaining=0.01)
    assert early > late, f"spread should shrink as the session winds down: early={early}, late={late}"


def test_spread_widens_with_higher_volatility():
    m = _maker()
    calm = m.optimal_half_spread_ticks(sigma_ticks=1.0, time_remaining=0.5)
    volatile = m.optimal_half_spread_ticks(sigma_ticks=10.0, time_remaining=0.5)
    assert volatile > calm, "higher volatility must widen the protective spread"


def test_spread_never_crosses_or_locks():
    """Regardless of parameters, bid must stay strictly below ask -- this is
    the same invariant the Go book itself enforces server-side, checked here
    on the client-side quoting logic before it ever reaches the wire."""
    for gamma in (0.01, 0.5, 5.0):
        for inv in (-500, 0, 500):
            m = _maker(params=AvellanedaStoikovParams(gamma=gamma, k=1.5))
            m.inventory = inv
            r = m.reservation_price_ticks(10000, 5.0, 0.5)
            hs = m.optimal_half_spread_ticks(5.0, 0.5)
            bid, ask = round(r - hs), round(r + hs)
            assert bid < ask, f"gamma={gamma} inv={inv}: bid={bid} >= ask={ask}"


def test_estimate_k_recovers_known_decay_rate():
    """Synthesize depth data from EXACTLY the model's own assumed shape
    (A * exp(-k * delta)) with a known k, and confirm the log-linear fit
    recovers it -- this validates the ESTIMATOR, separately from the
    quoting formulas above."""
    true_k = 0.8
    A = 100.0
    depth = {delta: A * np.exp(-true_k * delta) for delta in range(1, 20)}
    fitted_k = estimate_k(depth)
    assert fitted_k == pytest.approx(true_k, rel=0.05)


def test_estimate_k_falls_back_gracefully_on_degenerate_input():
    assert estimate_k({}) > 0
    assert estimate_k({5: 10.0}) > 0
    # Non-decaying (flat) depth doesn't match the model's assumption; must
    # still return a positive, usable k rather than a negative or zero one.
    flat = {d: 50.0 for d in range(1, 10)}
    assert estimate_k(flat) > 0


def test_fill_attribution_matches_heuristic_maker_semantics():
    """A resting buy hit by a sell taker increases inventory; a resting sell
    hit by a buy taker decreases it -- must behave identically to
    agents.MarketMaker.on_fill, since the simulation's fill-routing logic
    (simulate.py's _route_fills) is written against that shared contract."""
    m = _maker()
    m.on_fill("buy", 50, 10000)
    assert m.inventory == 50 and m.cash == -50 * 10000
    m.on_fill("sell", 20, 10100)
    assert m.inventory == 30 and m.cash == -50 * 10000 + 20 * 10100
