"""API-level tests for /pairs -- overview, analytics, and force-close.

Seeding enough price history: the `client` fixture's app registry starts
each SymbolMarket with price_history=[s0] (length 1, see app/markets.py),
far short of PairsCointegrationStrategy's default min_history=90. Rather
than ticking the real simulated market hundreds of times (slow, and its
two symbols' price processes aren't cointegrated by construction anyway --
see pairs_cointegration.py's own docstring on why correlation isn't
enough), tests that need real stats overwrite price_history directly with
a synthetic, genuinely cointegrated pair -- the engine's actual order book
(`market.eng`) is untouched, so order submission/matching still works
exactly as in test_brackets_api.py.
"""

import numpy as np

from app.main import app


def _cointegrated_pair(n=300, seed=0):
    rng = np.random.default_rng(seed)
    b = 100 + np.cumsum(rng.normal(0, 0.5, n))
    noise = rng.normal(0, 0.3, n)
    a = b + 5.0 + noise
    return a, b


def _seed_price_history(n=300, seed=0):
    a, b = _cointegrated_pair(n=n, seed=seed)
    app.state.registry.markets["ICICIBANK"].price_history = list(a)
    app.state.registry.markets["HDFCBANK"].price_history = list(b)


def _seed_independent_price_history(n=300, seed=0):
    """Two unrelated random walks -- NOT cointegrated by construction,
    unlike _cointegrated_pair's deliberately extreme spread. This is what
    surfaces a numpy.bool_ leaking into the JSON response: a very strongly
    cointegrated pair's p-value gets clipped by statsmodels to a plain
    python float, which happens to serialize fine and hides the bug --
    confirmed directly against the running dev server, not just suspected."""
    rng = np.random.default_rng(seed)
    a = 100 + np.cumsum(rng.normal(0, 1.0, n))
    b = 50 + np.cumsum(rng.normal(0, 1.0, n))
    app.state.registry.markets["ICICIBANK"].price_history = list(a)
    app.state.registry.markets["HDFCBANK"].price_history = list(b)


def test_overview_reports_warming_up_with_default_fresh_registry(client):
    resp = client.get("/pairs/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["warming_up"] is True
    assert body["zscore"] is None
    assert body["position"] == "none"
    assert body["legs"] == {}
    assert body["activity"] == []


def test_overview_returns_live_stats_once_history_is_seeded(client):
    _seed_price_history()
    resp = client.get("/pairs/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["warming_up"] is False
    assert isinstance(body["zscore"], float)
    assert isinstance(body["hedge_ratio"], float)
    assert body["cointegration_pvalue"] < 0.05
    assert body["is_cointegrated"] is True
    assert body["symbol_a"] == "ICICIBANK"
    assert body["symbol_b"] == "HDFCBANK"
    assert body["config"]["entry_z"] == 1.5


def test_analytics_series_are_capped_at_default_series_length(client):
    _seed_price_history(n=500)
    resp = client.get("/pairs/analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["warming_up"] is False
    assert len(body["zscore_series"]) == 300
    assert len(body["hedge_ratio_series"]) == 300
    assert body["entry_z"] == 1.5
    assert body["stop_z"] == 3.0


def test_overview_serializes_cleanly_for_a_non_cointegrated_pair(client):
    """The regression case: near-independent price series produce a
    non-clipped, non-extreme p-value, which is exactly the shape of
    is_cointegrated that broke JSON serialization before it was cast to a
    plain bool."""
    _seed_independent_price_history()
    resp = client.get("/pairs/overview")
    assert resp.status_code == 200
    assert resp.json()["is_cointegrated"] in (True, False)


def test_analytics_serializes_cleanly_for_a_non_cointegrated_pair(client):
    _seed_independent_price_history()
    resp = client.get("/pairs/analytics")
    assert resp.status_code == 200
    assert resp.json()["is_cointegrated"] in (True, False)


def test_force_close_is_rejected_with_no_open_position(client):
    resp = client.post("/pairs/close")
    assert resp.status_code == 400


def test_force_close_flattens_an_open_position(client):
    buy_a = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
        "strategy_key": "pairs_cointegration",
    }).json()
    sell_b = client.post("/orders", json={
        "symbol": "HDFCBANK", "side": "sell", "order_type": "market", "qty": 5,
        "strategy_key": "pairs_cointegration",
    }).json()
    assert buy_a["filled_qty"] > 0 and sell_b["filled_qty"] > 0

    overview = client.get("/pairs/overview").json()
    assert overview["position"] == "long_spread"
    assert "ICICIBANK" in overview["legs"] and "HDFCBANK" in overview["legs"]

    resp = client.post("/pairs/close")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    overview_after = client.get("/pairs/overview").json()
    assert overview_after["position"] == "none"
    assert overview_after["legs"] == {}
