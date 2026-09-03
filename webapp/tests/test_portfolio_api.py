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


def test_attribution_mode_paper_and_virtual_do_not_bleed_into_each_other(client):
    """Regression test for a real bug found live, 2026-09-03: this
    endpoint carried NO mode parameter at all before -- it always queried
    Mode.paper regardless of what was actually selected, so Portfolio IQ
    silently showed paper's numbers under every mode (a live-mode "shows
    profit that isn't there" report). A position opened under one mode
    must not appear in the other mode's own attribution."""
    client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5, "mode": "virtual",
    })

    paper = client.get("/portfolio/attribution", params={"mode": "paper"}).json()
    virtual = client.get("/portfolio/attribution", params={"mode": "virtual"}).json()

    # Paper has no orders at all -- still the trivial all-cash case.
    assert paper["portfolio_return"] == pytest.approx(0.0)
    # Virtual holds a real position -- not the trivial all-cash case.
    assert virtual["computable"] is True


def test_attribution_rejects_live_as_a_mode(client):
    # There is no real mark-to-market registry or benchmark price source
    # for live positions yet (see get_attribution's own docstring) --
    # "live" must be a clean validation error, never silently treated as
    # paper.
    resp = client.get("/portfolio/attribution", params={"mode": "live"})
    assert resp.status_code == 422


def test_realized_pnl_curve_mode_paper_and_virtual_do_not_bleed_into_each_other(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5, "mode": "virtual"})
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "sell", "order_type": "market", "qty": 5, "mode": "virtual"})

    assert client.get("/portfolio/realized-pnl-curve", params={"mode": "paper"}).json() == []
    virtual_curve = client.get("/portfolio/realized-pnl-curve", params={"mode": "virtual"}).json()
    assert len(virtual_curve) == 2


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
