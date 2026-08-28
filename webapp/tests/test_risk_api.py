"""GET creates the user's default RiskSettings row on first access, PUT
applies a partial update, and reset-halt is the one explicit way to clear
a circuit-breaker halt."""


def test_get_risk_controls_creates_and_returns_the_default_row(client):
    resp = client.get("/risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_order_qty"] == 500
    assert body["kelly_multiplier"] == 0.25
    assert body["max_position_fraction"] == 0.20
    assert body["daily_max_drawdown_pct"] == 5.0
    assert body["pairs_entry_z"] == 1.5
    assert body["pairs_exit_z"] == 0.1
    assert body["pairs_stop_z"] == 3.0
    assert body["coint_pvalue_max"] == 0.05
    assert body["trading_halted"] is False


def test_get_risk_controls_is_idempotent_not_a_fresh_row_each_time(client):
    """The get-or-create must find the SAME row on a second call, not
    silently create a duplicate -- verified by changing a value via PUT
    and confirming a subsequent GET still sees it, not the default."""
    client.put("/risk", json={"kelly_multiplier": 0.4})
    resp = client.get("/risk")
    assert resp.json()["kelly_multiplier"] == 0.4


def test_put_updates_only_the_supplied_fields(client):
    before = client.get("/risk").json()

    resp = client.put("/risk", json={"kelly_multiplier": 0.5, "max_order_qty": 200})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kelly_multiplier"] == 0.5
    assert body["max_order_qty"] == 200
    # Everything NOT mentioned in the PUT body must be untouched.
    assert body["max_position_fraction"] == before["max_position_fraction"]
    assert body["daily_max_drawdown_pct"] == before["daily_max_drawdown_pct"]
    assert body["pairs_entry_z"] == before["pairs_entry_z"]
    assert body["pairs_exit_z"] == before["pairs_exit_z"]
    assert body["pairs_stop_z"] == before["pairs_stop_z"]
    assert body["coint_pvalue_max"] == before["coint_pvalue_max"]


def test_put_round_trips_through_a_subsequent_get(client):
    client.put("/risk", json={"daily_max_drawdown_pct": 8.5, "pairs_stop_z": 4.0})
    resp = client.get("/risk")
    body = resp.json()
    assert body["daily_max_drawdown_pct"] == 8.5
    assert body["pairs_stop_z"] == 4.0


def test_put_with_empty_body_is_a_no_op(client):
    before = client.get("/risk").json()
    resp = client.put("/risk", json={})
    assert resp.status_code == 200
    after = resp.json()
    for key in before:
        if key == "trading_halted":
            continue
        assert after[key] == before[key], key


def test_reset_halt_clears_the_flag(client):
    from app.models.risk import RiskSettings

    # Force a halt directly (bypassing the circuit breaker itself, which
    # is tested separately) -- this test is only about the reset endpoint.
    client.get("/risk")  # ensure the row exists
    db = client.db_session_factory()
    try:
        settings = db.query(RiskSettings).first()
        settings.trading_halted = True
        db.commit()
    finally:
        db.close()

    resp = client.get("/risk")
    assert resp.json()["trading_halted"] is True

    reset_resp = client.post("/risk/reset-halt")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["trading_halted"] is False

    resp2 = client.get("/risk")
    assert resp2.json()["trading_halted"] is False


def test_put_cannot_set_trading_halted_directly(client):
    """trading_halted is deliberately not a field on the update payload --
    only the circuit breaker (setting it) and POST /reset-halt (clearing
    it) may change it. A PUT that tries anyway must simply ignore the
    extra field, not error and not apply it."""
    resp = client.put("/risk", json={"trading_halted": True, "kelly_multiplier": 0.3})
    assert resp.status_code == 200
    assert resp.json()["trading_halted"] is False
    assert resp.json()["kelly_multiplier"] == 0.3
