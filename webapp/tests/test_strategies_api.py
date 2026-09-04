def test_list_strategies_includes_the_4_signal_strategies(client):
    resp = client.get("/strategies")
    assert resp.status_code == 200
    keys = {s["key"] for s in resp.json()}
    # market_maker is deliberately excluded here -- see strategy_runner.py's
    # own docstring on why (it already runs continuously inside every
    # SymbolMarket, not as a user-selectable periodic-signal strategy).
    assert "alpha_rsi_ema" in keys
    assert "momentum_macd" in keys
    assert "mean_reversion_bb" in keys
    assert "pairs_cointegration" in keys
    assert "market_maker" not in keys


def test_list_strategies_reports_fixed_underlying_for_pairs_and_options_only(client):
    """Regression test for a real bug found live via a Playwright
    walkthrough, 2026-09-04: Strategies.js's symbol picker rendered as an
    editable dropdown for OPTIONS strategies too (iron_condor,
    calendar_spread, short_strangle, delta_neutral), even though each one
    trades a fixed underlying and silently ignores whatever symbol the
    UI submitted -- misleading, since a user could believe they'd
    switched Iron Condor to a different underlying and nothing would
    actually change. fixed_underlying is the fix's real data: non-null
    for pairs/options (naming exactly what's actually fixed), null for
    single_instrument strategies (which have a real, editable `symbol`
    instead)."""
    by_key = {s["key"]: s for s in client.get("/strategies").json()}

    assert by_key["alpha_rsi_ema"]["kind"] == "single_instrument"
    assert by_key["alpha_rsi_ema"]["fixed_underlying"] is None

    assert by_key["pairs_cointegration"]["kind"] == "pairs"
    assert by_key["pairs_cointegration"]["fixed_underlying"] == "ICICIBANK / HDFCBANK"

    # Each options strategy has its OWN underlying, not a shared default.
    assert by_key["iron_condor"]["fixed_underlying"] == "NIFTY50"
    assert by_key["calendar_spread"]["fixed_underlying"] == "NIFTY50"
    assert by_key["short_strangle"]["fixed_underlying"] == "BANKNIFTY"
    assert by_key["delta_neutral"]["fixed_underlying"] == "ICICIBANK"


def test_no_allocations_initially(client):
    resp = client.get("/strategies/allocations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_enabling_a_single_instrument_strategy_requires_a_symbol(client):
    resp = client.put("/strategies/allocations/alpha_rsi_ema", json={"enabled": True, "weight": 0.5})
    assert resp.status_code == 400


def test_enabling_a_single_instrument_strategy_with_a_symbol_succeeds(client):
    resp = client.put("/strategies/allocations/alpha_rsi_ema", json={
        "enabled": True, "weight": 0.5, "symbol": "ICICIBANK",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["symbol"] == "ICICIBANK"

    listing = client.get("/strategies/allocations").json()
    assert len(listing) == 1
    assert listing[0]["strategy_key"] == "alpha_rsi_ema"


def test_unknown_symbol_is_rejected(client):
    resp = client.put("/strategies/allocations/alpha_rsi_ema", json={
        "enabled": True, "symbol": "NOTASYMBOL",
    })
    assert resp.status_code == 404


def test_unknown_strategy_key_is_rejected(client):
    resp = client.put("/strategies/allocations/totally_made_up", json={"enabled": True, "symbol": "ICICIBANK"})
    assert resp.status_code == 404


def test_pairs_strategy_does_not_require_a_symbol(client):
    resp = client.put("/strategies/allocations/pairs_cointegration", json={"enabled": True, "weight": 1.0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["symbol"] is None


def test_live_mode_allocation_is_rejected_with_501(client):
    resp = client.put("/strategies/allocations/alpha_rsi_ema", json={
        "enabled": True, "symbol": "ICICIBANK", "mode": "live",
    })
    assert resp.status_code == 501


def test_setting_an_allocation_twice_updates_it_rather_than_duplicating(client):
    client.put("/strategies/allocations/alpha_rsi_ema", json={"enabled": True, "symbol": "ICICIBANK"})
    client.put("/strategies/allocations/alpha_rsi_ema", json={"enabled": False, "symbol": "TCS"})
    listing = client.get("/strategies/allocations").json()
    assert len(listing) == 1
    assert listing[0]["enabled"] is False
    assert listing[0]["symbol"] == "TCS"
