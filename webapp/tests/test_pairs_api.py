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
from app.pairs_service import refresh_pair_telemetry_once


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


# --- Stationarity telemetry (ADF / Johansen / Hurst / half-life) --------
#
# Computed on the tick cadence (app.pairs_service.refresh_pair_telemetry_once),
# not per request -- tests call it directly since DISABLE_MARKET_TICK=1
# means nothing calls it automatically here.

def test_telemetry_is_null_before_any_tick_has_computed_it(client):
    resp = client.get("/pairs/overview")
    assert resp.json()["telemetry"] is None


def test_telemetry_populates_after_a_refresh_and_carries_interpretation_context(client):
    _seed_price_history()
    refresh_pair_telemetry_once(app.state.registry)

    for path in ("/pairs/overview", "/pairs/analytics"):
        body = client.get(path).json()
        telemetry = body["telemetry"]
        assert telemetry is not None, path
        assert telemetry["n_points"] > 0
        assert telemetry["age_seconds"] >= 0

        adf = telemetry["adf"]
        assert isinstance(adf["stat"], float)
        assert isinstance(adf["pvalue"], float)
        assert set(adf["critical_values"].keys()) == {"1%", "5%", "10%"}
        assert isinstance(adf["is_stationary"], bool)

        johansen = telemetry["johansen"]
        assert len(johansen["trace_stats"]) == 2
        assert len(johansen["critical_values_90"]) == 2
        assert len(johansen["critical_values_95"]) == 2
        assert len(johansen["critical_values_99"]) == 2

        assert telemetry["hurst"]["random_walk_reference"] == 0.5
        assert isinstance(telemetry["hurst"]["value"], float)
        assert isinstance(telemetry["half_life_bars"], float)


def test_telemetry_correctly_identifies_a_deliberately_cointegrated_spread_as_stationary(client):
    _seed_price_history()  # _cointegrated_pair -- a IS b+5+small_noise, by construction
    refresh_pair_telemetry_once(app.state.registry)

    telemetry = client.get("/pairs/overview").json()["telemetry"]
    assert telemetry["adf"]["is_stationary"] is True
    assert telemetry["adf"]["pvalue"] < 0.05
    # A deliberately tight, mean-reverting spread should read well below
    # the H=0.5 random-walk reference, not just "some float."
    assert telemetry["hurst"]["value"] < 0.5


def test_overview_and_analytics_read_the_same_cached_telemetry(client):
    """Proves this is a shared cache read, not two independent
    recomputations that could silently drift from each other -- the whole
    point of computing it once on the tick cadence."""
    _seed_price_history()
    refresh_pair_telemetry_once(app.state.registry)

    overview_telemetry = client.get("/pairs/overview").json()["telemetry"]
    analytics_telemetry = client.get("/pairs/analytics").json()["telemetry"]
    assert overview_telemetry["computed_at"] == analytics_telemetry["computed_at"]
    assert overview_telemetry["adf"]["stat"] == analytics_telemetry["adf"]["stat"]


def test_telemetry_cache_does_not_leak_across_app_lifespans():
    """Regression test for a real bug: _pair_telemetry is a module-level
    global, but each TestClient(app) lifespan creates a FRESH
    MarketRegistry -- without an explicit reset on startup (app/main.py's
    lifespan calling reset_pair_telemetry()), a later test would see an
    earlier test's stale cached telemetry, computed against a registry
    that no longer exists."""
    import os
    os.environ.setdefault("DISABLE_MARKET_TICK", "1")
    os.environ.setdefault("DISABLE_AUTO_MIGRATE", "1")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base, get_db

    def _fresh_client():
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(bind=engine)
        app.dependency_overrides[get_db] = lambda: (yield SessionLocal())
        return TestClient(app)

    with _fresh_client() as c1:
        _seed_price_history()
        refresh_pair_telemetry_once(app.state.registry)
        assert c1.get("/pairs/overview").json()["telemetry"] is not None

    with _fresh_client() as c2:
        # A fresh lifespan started -- the previous one's cached telemetry
        # must NOT still be visible.
        assert c2.get("/pairs/overview").json()["telemetry"] is None
