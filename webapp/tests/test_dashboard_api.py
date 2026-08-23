import pytest


def test_stats_with_no_trades(client):
    resp = client.get("/dashboard/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_trades"] == 0
    assert body["win_rate"] is None
    assert body["net_pnl"] == 0.0


def test_stats_reflect_a_closed_round_trip(client):
    # DISABLE_MARKET_TICK=1 in tests (see conftest.py) means the book stays
    # exactly as seeded -- a market buy against the seed sell fills fully
    # and deterministically, same as test_orders_api.py already relies on.
    buy = client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5}).json()
    assert buy["filled_qty"] == 5
    sell = client.post("/orders", json={"symbol": "ICICIBANK", "side": "sell", "order_type": "market", "qty": 5}).json()
    assert sell["filled_qty"] == 5

    stats = client.get("/dashboard/stats").json()
    assert stats["n_trades"] == 1
    assert stats["win_rate"] in (0.0, 1.0)  # exactly one closed trade -- it's either a win or a loss
    # Bought then sold at the seed's bid/ask, one tick apart -- a real loss
    # from crossing the spread twice, not a profit: buy fills at the seed
    # ask (higher), sell fills at the seed bid (lower).
    assert stats["net_pnl"] < 0

    calendar = client.get("/dashboard/calendar").json()
    assert len(calendar) == 1
    assert calendar[0]["n_trades"] == 1
    assert calendar[0]["pnl"] == pytest.approx(stats["net_pnl"])


def test_calendar_starts_empty(client):
    resp = client.get("/dashboard/calendar")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_list_and_delete_a_note(client):
    create = client.post("/dashboard/notes", json={"text": "Closed all ICICI before the weekend."})
    assert create.status_code == 200, create.text
    note = create.json()
    assert note["text"] == "Closed all ICICI before the weekend."

    listing = client.get("/dashboard/notes").json()
    assert len(listing) == 1
    assert listing[0]["id"] == note["id"]

    delete = client.delete(f"/dashboard/notes/{note['id']}")
    assert delete.status_code == 200

    listing_after = client.get("/dashboard/notes").json()
    assert listing_after == []


def test_empty_note_text_is_rejected(client):
    resp = client.post("/dashboard/notes", json={"text": "   "})
    assert resp.status_code == 400


def test_deleting_a_nonexistent_note_is_a_404(client):
    resp = client.delete("/dashboard/notes/99999")
    assert resp.status_code == 404


def test_notes_are_newest_first(client):
    client.post("/dashboard/notes", json={"text": "first"})
    client.post("/dashboard/notes", json={"text": "second"})
    listing = client.get("/dashboard/notes").json()
    assert listing[0]["text"] == "second"
    assert listing[1]["text"] == "first"
