"""GET /market/history -- the chart-seeding endpoint (app/routers/market.py).
See that module's own docstring for why this exists: without it, a chart
with a long candle interval (e.g. 1hr) is blank for up to an hour on first
load, since the live tick stream is the only other source of bars."""

from app.main import app


def _step(symbol: str, n: int = 1):
    market = app.state.registry.markets[symbol]
    for _ in range(n):
        market.step()
    return market


def test_history_for_a_fresh_market_returns_the_seed_point_as_one_bar(client):
    resp = client.get("/market/history", params={"symbol": "ICICIBANK", "interval": "1s", "limit": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "ICICIBANK"
    assert body["interval"] == "1s"
    assert body["requested_bars"] == 10
    # A fresh market has exactly ONE price_history point (the seed s0) --
    # never padded with synthetic bars to make returned_bars look like 10.
    assert body["returned_bars"] == 1
    assert len(body["bars"]) == 1
    bar = body["bars"][0]
    assert bar["open"] == bar["high"] == bar["low"] == bar["close"]
    assert bar["open"] > 0


def test_history_bucketing_aggregates_multiple_seconds_into_one_minute_bar(client):
    _step("ICICIBANK", 65)  # a bit over one full minute of 1-second steps
    resp = client.get("/market/history", params={"symbol": "ICICIBANK", "interval": "1m", "limit": 10})
    body = resp.json()
    # 66 total price points (1 seed + 65 steps) bucketed into 1-minute
    # (60s) wall-clock-aligned buckets -- either 2 or 3 bars depending on
    # exactly where "now" falls relative to a minute boundary at test run
    # time, but never 66 (one bar per point, i.e. no aggregation at all).
    assert 1 <= body["returned_bars"] <= 3
    for bar in body["bars"]:
        assert bar["high"] >= bar["low"]
        assert bar["high"] >= bar["open"]
        assert bar["high"] >= bar["close"]
        assert bar["low"] <= bar["open"]
        assert bar["low"] <= bar["close"]


def test_history_bars_are_ordered_oldest_to_newest(client):
    _step("ICICIBANK", 130)
    resp = client.get("/market/history", params={"symbol": "ICICIBANK", "interval": "1m", "limit": 50})
    bars = resp.json()["bars"]
    timestamps = [b["timestamp"] for b in bars]
    assert timestamps == sorted(timestamps)


def test_history_respects_the_limit(client):
    _step("ICICIBANK", 300)
    resp = client.get("/market/history", params={"symbol": "ICICIBANK", "interval": "1s", "limit": 5})
    body = resp.json()
    assert body["returned_bars"] == 5
    assert len(body["bars"]) == 5
    # The most recent 5 seconds' worth, not an arbitrary slice.
    assert body["bars"][-1]["close"] == app.state.registry.markets["ICICIBANK"].current_price


def test_history_limit_over_the_ceiling_is_rejected(client):
    resp = client.get("/market/history", params={"symbol": "ICICIBANK", "interval": "1s", "limit": 100_000})
    assert resp.status_code == 422


def test_history_unknown_symbol_is_a_404(client):
    resp = client.get("/market/history", params={"symbol": "NOTASYMBOL", "interval": "1m"})
    assert resp.status_code == 404


def test_history_for_a_derived_index_has_no_volume(client):
    _step("ICICIBANK", 5)
    _step("HDFCBANK", 5)
    _step("SBIN", 5)
    resp = client.get("/market/history", params={"symbol": "BANKNIFTY", "interval": "1s", "limit": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "BANKNIFTY"
    for bar in body["bars"]:
        assert bar["volume"] is None
        assert bar["close"] > 0


def test_history_real_instrument_reports_real_volume_within_the_recent_window(client):
    _step("ICICIBANK", 5)
    resp = client.get("/market/history", params={"symbol": "ICICIBANK", "interval": "1s", "limit": 10})
    bars = resp.json()["bars"]
    # price_history's very FIRST point is the seed price s0, before any
    # step() ever ran -- nothing traded to produce it, so it genuinely has
    # no volume (None is correct there, not a windowing artifact). Every
    # bar built from an actual step(), which recent_volume covers in full
    # here (well under its 500-entry window), must have a real volume.
    assert bars[0]["volume"] is None
    assert all(bar["volume"] is not None for bar in bars[1:])


def test_history_old_volume_outside_the_recent_window_is_none_not_zero(client):
    """recent_volume is bounded (maxlen=500, app/markets.py) but
    price_history is not -- a bar built from points older than the volume
    window must report None, not a fabricated 0, since 0 would claim
    'we know no volume traded' rather than 'we don't have that figure
    anymore'."""
    _step("ICICIBANK", 510)
    resp = client.get("/market/history", params={"symbol": "ICICIBANK", "interval": "1s", "limit": 600})
    bars = resp.json()["bars"]
    oldest = bars[0]
    newest = bars[-1]
    assert oldest["volume"] is None
    assert newest["volume"] is not None
