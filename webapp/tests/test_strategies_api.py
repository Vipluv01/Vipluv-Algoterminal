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
