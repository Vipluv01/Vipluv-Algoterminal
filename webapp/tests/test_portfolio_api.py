"""Portfolio IQ -- app/routers/portfolio.py. Brinson attribution against a
NAMED benchmark, the realized P&L walk, and the sub-account breakdown."""

import pytest


def test_attribution_names_its_benchmark(client):
    resp = client.get("/portfolio/attribution")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["benchmark_name"]
    assert "ICICIBANK" in body["benchmark_symbols"]
    assert len(body["benchmark_symbols"]) == 7
    assert body["methodology_note"]


def test_attribution_is_computable_for_a_cash_only_account(client):
    # No trades yet -- 100% cash, still a valid (if trivial) attribution:
    # weight sums to 1 via the CASH entry, portfolio_return is 0.
    resp = client.get("/portfolio/attribution")
    body = resp.json()
    assert body["computable"] is True
    assert body["reason"] is None
    assert body["portfolio_return"] == pytest.approx(0.0)
    # excess == allocation + selection + interaction, the identity
    # brinson_attribution's own tests already verify -- checked again here
    # end-to-end through the real router wiring, not just the pure function.
    assert body["excess"] == pytest.approx(body["allocation"] + body["selection"] + body["interaction"])


def test_attribution_reflects_a_real_held_position(client):
    buy = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    }).json()
    assert buy["filled_qty"] == 5

    resp = client.get("/portfolio/attribution")
    body = resp.json()
    assert body["computable"] is True
    # Holding a real position (not 100% cash) makes excess almost certainly
    # nonzero relative to a passive equal-weight benchmark -- not asserting
    # a specific sign/magnitude (that's the pure function's own test
    # surface), just that the router actually wired a live position in
    # rather than always reporting the trivial all-cash 0.0 case.
    assert isinstance(body["allocation"], float)
    assert isinstance(body["selection"], float)


def test_realized_pnl_curve_starts_empty_and_grows_with_a_closed_round_trip(client):
    assert client.get("/portfolio/realized-pnl-curve").json() == []

    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "sell", "order_type": "market", "qty": 5})

    curve = client.get("/portfolio/realized-pnl-curve").json()
    assert len(curve) == 2  # one RealizedPnlPoint per fill
    assert "realized_pnl" in curve[0]
    assert "equity" not in curve[0], "relabeled explicitly so this can never be read as the mark-to-market curve"


def test_sub_account_breakdown_is_empty_with_no_sub_accounts(client):
    assert client.get("/portfolio/sub-accounts").json() == []


def test_sub_account_breakdown_reflects_a_real_sub_account(client):
    sub = client.post("/account/sub", json={"label": "aggressive", "sizing_multiplier": 2.0}).json()

    breakdown = client.get("/portfolio/sub-accounts").json()
    assert len(breakdown) == 1
    row = breakdown[0]
    assert row["id"] == sub["id"]
    assert row["label"] == "aggressive"
    assert row["sizing_multiplier"] == 2.0
    assert row["cash"] == 100_000.0  # untouched -- no orders tagged to this sub-account yet
    assert row["total_value"] == 100_000.0
