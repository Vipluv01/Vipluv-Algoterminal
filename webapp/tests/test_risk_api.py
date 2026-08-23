def test_risk_controls_reflect_the_actual_configured_values(client):
    """Not placeholder numbers -- these must match position_sizing.py's
    and pairs_cointegration.py's real defaults, since the whole point of
    this endpoint is showing what the system is actually running with."""
    resp = client.get("/risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_order_qty"] == 100_000
    assert body["kelly_multiplier"] == 0.25
    assert body["max_position_fraction"] == 0.5
    assert body["pairs_entry_z"] == 1.5
    assert body["pairs_stop_z"] == 3.0
    assert body["pairs_coint_pvalue_max"] == 0.05
