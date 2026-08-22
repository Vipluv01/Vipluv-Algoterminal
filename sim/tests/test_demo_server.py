"""Tests for LiveSim -- the interactive simulation driving the browser demo.

These test the SAME class the WebSocket server uses directly (not through a
socket), since the interesting logic (state transitions, JSON-safety of
every step's output) has nothing to do with the network layer.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

from demo_server import LiveSim


def test_step_produces_json_serializable_output():
    sim = LiveSim(seed=1)
    try:
        for _ in range(20):
            msg = sim.step()
            json.dumps(msg)  # raises if anything (e.g. a numpy scalar) leaked through
    finally:
        sim.close()


def test_step_count_increments():
    sim = LiveSim(seed=1)
    try:
        assert sim.step_count == 0
        sim.step()
        assert sim.step_count == 1
        sim.step()
        assert sim.step_count == 2
    finally:
        sim.close()


def test_add_informed_trader_increases_population():
    sim = LiveSim(seed=1)
    try:
        n0 = len(sim.informed_traders)
        sim.add_informed_trader()
        assert len(sim.informed_traders) == n0 + 1
        # The new trader must have a distinct id from every existing one --
        # a collision would mean its fills get misattributed via
        # owner_of_order_id.
        ids = [t.trader_id for t in sim.informed_traders]
        assert len(ids) == len(set(ids))
    finally:
        sim.close()


def test_set_volatility_is_clamped_to_a_sane_range():
    sim = LiveSim(seed=1)
    try:
        sim.set_volatility(1000.0)
        assert sim.fundamental.sigma <= 2.0
        sim.set_volatility(-5.0)
        assert sim.fundamental.sigma >= 0.01
    finally:
        sim.close()


def test_depth_and_maker_stats_present_every_step():
    """The frontend indexes msg["maker"]["inventory"] etc. unconditionally --
    these keys must never be missing, even on step 1 before any fills."""
    sim = LiveSim(seed=2)
    try:
        msg = sim.step()
        assert "bids" in msg and "asks" in msg and "trades" in msg
        assert set(msg["maker"].keys()) == {"inventory", "pnl", "spread_ticks"}
    finally:
        sim.close()
