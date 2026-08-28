"""GET /telemetry/latency -- app/routers/telemetry.py, backed by
app/telemetry.py's rolling order-submit latency tracker. Real Python<->
simserver IPC round-trip, timed in app/markets.py's own submit wrapper --
NOT the Go benchmark's in-process numbers (see app/telemetry.py's own
docstring on why those measure something different)."""

import time

from app.main import app


def test_latency_is_null_before_any_order_has_been_submitted(client):
    resp = client.get("/telemetry/latency")
    assert resp.status_code == 200
    assert resp.json() is None


def test_latency_reports_real_percentiles_after_a_real_order(client):
    submit = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    })
    assert submit.status_code == 200, submit.text

    body = client.get("/telemetry/latency").json()
    assert body is not None
    assert body["n_samples"] >= 1
    assert body["p50_ms"] >= 0.0
    assert body["p99_ms"] >= 0.0
    assert body["p99_ms"] >= body["p50_ms"]  # p99 can never be below p50 by definition


def test_latency_sample_count_grows_with_more_submits(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 1})
    first = client.get("/telemetry/latency").json()

    client.post("/orders", json={"symbol": "HDFCBANK", "side": "buy", "order_type": "market", "qty": 1})
    client.post("/orders", json={"symbol": "RELIANCE", "side": "buy", "order_type": "market", "qty": 1})
    second = client.get("/telemetry/latency").json()

    assert second["n_samples"] > first["n_samples"]


def test_latency_never_reuses_the_go_benchmark_scale():
    """Regression guard against exactly the mistake this feature exists to
    avoid: the Go bench figures (results/latency.json) are nanoseconds,
    typically double-digit ns for a book op. A real Python subprocess IPC
    round-trip is milliseconds -- many orders of magnitude slower. If a
    future change accidentally wired this endpoint to read the Go numbers
    (or divided/scaled them to LOOK like ms), the reported latency would
    be implausibly fast for a real cross-process round-trip."""
    import os
    os.environ.setdefault("DISABLE_MARKET_TICK", "1")
    os.environ.setdefault("DISABLE_AUTO_MIGRATE", "1")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import Base, get_db

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = lambda: (yield SessionLocal())

    with TestClient(app) as c:
        c.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
        body = c.get("/telemetry/latency").json()
        # A REAL subprocess round-trip is easily >0.01ms; this just rules
        # out "accidentally reporting a nanosecond figure unconverted,"
        # not asserting a specific performance target.
        assert body["p50_ms"] > 0.001


def test_latency_cache_does_not_leak_across_app_lifespans():
    """Same class of bug as the pair-telemetry cache leak: a module-level
    global that must be reset per lifespan, or a later test/process would
    see an earlier one's stale samples."""
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
        c1.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
        assert c1.get("/telemetry/latency").json() is not None

    with _fresh_client() as c2:
        assert c2.get("/telemetry/latency").json() is None


def test_ws_tick_payload_carries_a_real_sent_at_timestamp(client):
    with client.websocket_connect("/ws/market/ICICIBANK") as ws:
        before_ms = int(time.time() * 1000)
        msg = ws.receive_json()
        after_ms = int(time.time() * 1000)
        assert "sent_at" in msg
        assert before_ms - 1000 <= msg["sent_at"] <= after_ms + 1000


def test_ws_tick_payload_for_a_derived_index_also_carries_sent_at(client):
    with client.websocket_connect("/ws/market/NIFTY50") as ws:
        msg = ws.receive_json()
        assert "sent_at" in msg
        assert msg["sent_at"] > 0
