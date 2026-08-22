"""Tests for LiveSim -- the interactive simulation driving the browser demo.

These test the SAME class the WebSocket server uses directly (not through a
socket), since the interesting logic (state transitions, JSON-safety of
every step's output) has nothing to do with the network layer.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

from websockets.datastructures import Headers
from websockets.http11 import Request

from demo_server import MAX_INFORMED_TRADERS, LiveSim, serve_static_or_upgrade


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


def test_add_informed_trader_is_capped():
    """Regression test: add_informed_trader() is reachable by any connected
    browser tab once this server is public, not just a trusted operator --
    an earlier version had no bound, so a single misbehaving or malicious
    client could spam this command and grow per-step work without limit for
    every visitor watching the same live demo."""
    sim = LiveSim(seed=1)
    try:
        for _ in range(MAX_INFORMED_TRADERS + 20):
            sim.add_informed_trader()
        assert len(sim.informed_traders) == MAX_INFORMED_TRADERS
    finally:
        sim.close()


def _request(path: str, upgrade: str | None) -> Request:
    headers = Headers()
    if upgrade is not None:
        headers["Upgrade"] = upgrade
    return Request(path=path, headers=headers)


def test_websocket_upgrade_request_falls_through_to_the_handshake():
    """Regression test for a real bug caught by hand while wiring up single-
    port deployment: an earlier version matched on request.path alone ("/"),
    which also matches the WebSocket handshake's own GET request -- so the
    server answered the handshake with a raw HTML body instead of letting
    it proceed, and the browser correctly rejected that as a failed upgrade.
    The page loaded fine (misleadingly looking like success) while the
    entire live feed silently never connected. Only the Upgrade header
    distinguishes the two, so this must return None (defer to the library's
    own handshake handling) whenever it's present."""
    req = _request("/", upgrade="websocket")
    result = asyncio.run(_maybe_await(serve_static_or_upgrade(None, req)))
    assert result is None


def test_plain_get_of_root_serves_the_static_page():
    req = _request("/", upgrade=None)
    result = asyncio.run(_maybe_await(serve_static_or_upgrade(None, req)))
    assert result is not None
    assert result.status_code == 200
    assert b"<!DOCTYPE html>" in bytes(result.body)


def test_healthz_does_not_require_the_full_page_body():
    req = _request("/healthz", upgrade=None)
    result = asyncio.run(_maybe_await(serve_static_or_upgrade(None, req)))
    assert result is not None
    assert result.status_code == 200
    assert bytes(result.body) == b"ok"


async def _maybe_await(value):
    return await value if asyncio.iscoroutine(value) else value
