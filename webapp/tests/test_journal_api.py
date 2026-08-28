"""Journal notes -- promoted from the Dashboard's own /dashboard/notes
panel; see app/routers/journal.py's module docstring."""


def test_create_list_and_delete_a_note(client):
    create = client.post("/journal/notes", json={"text": "Closed all ICICI before the weekend."})
    assert create.status_code == 200, create.text
    note = create.json()
    assert note["text"] == "Closed all ICICI before the weekend."
    assert note["tags"] is None
    assert note["trade_id"] is None
    assert note["pnl_snapshot"] == 0.0  # no trades yet -- a real, measured zero, not a placeholder

    listing = client.get("/journal/notes").json()
    assert len(listing) == 1
    assert listing[0]["id"] == note["id"]

    delete = client.delete(f"/journal/notes/{note['id']}")
    assert delete.status_code == 200

    listing_after = client.get("/journal/notes").json()
    assert listing_after == []


def test_empty_note_text_is_rejected(client):
    resp = client.post("/journal/notes", json={"text": "   "})
    assert resp.status_code == 400


def test_deleting_a_nonexistent_note_is_a_404(client):
    resp = client.delete("/journal/notes/99999")
    assert resp.status_code == 404


def test_notes_are_newest_first(client):
    client.post("/journal/notes", json={"text": "first"})
    client.post("/journal/notes", json={"text": "second"})
    listing = client.get("/journal/notes").json()
    assert listing[0]["text"] == "second"
    assert listing[1]["text"] == "first"


def test_note_can_carry_tags(client):
    create = client.post("/journal/notes", json={"text": "note", "tags": ["mistake", "fomo"]})
    assert create.json()["tags"] == ["mistake", "fomo"]
    assert client.get("/journal/notes").json()[0]["tags"] == ["mistake", "fomo"]


def test_note_can_link_to_a_real_trade(client):
    order = client.post("/orders", json={
        "symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5,
    }).json()

    create = client.post("/journal/notes", json={"text": "entered on the pullback", "trade_id": order["id"]})
    assert create.status_code == 200, create.text
    assert create.json()["trade_id"] == order["id"]


def test_note_linked_to_a_nonexistent_trade_is_rejected(client):
    resp = client.post("/journal/notes", json={"text": "note", "trade_id": 99999})
    assert resp.status_code == 404


def test_pnl_snapshot_reflects_realized_pnl_at_write_time_not_live(client):
    # No trades yet -- first note snapshots net_pnl == 0.0.
    first = client.post("/journal/notes", json={"text": "before any trades"}).json()
    assert first["pnl_snapshot"] == 0.0

    # A real, losing round trip (buy then sell crosses the spread twice --
    # same deterministic seed-book behavior test_dashboard_api.py relies on).
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "sell", "order_type": "market", "qty": 5})

    second = client.post("/journal/notes", json={"text": "after the round trip"}).json()
    assert second["pnl_snapshot"] < 0

    # The FIRST note's snapshot must not have silently changed after the
    # fact -- it's frozen at write time, not recomputed on read.
    refetched_first = next(n for n in client.get("/journal/notes").json() if n["id"] == first["id"])
    assert refetched_first["pnl_snapshot"] == 0.0


def test_note_script_tag_is_stripped(client):
    create = client.post("/journal/notes", json={"text": "<p>ok</p><script>alert(1)</script>"})
    assert create.status_code == 200, create.text
    assert "<script>" not in create.json()["text"]
    assert "alert(1)" not in create.json()["text"]


def test_note_that_is_only_a_malicious_payload_sanitizes_to_empty_and_is_rejected(client):
    resp = client.post("/journal/notes", json={"text": "<script>alert(1)</script>"})
    assert resp.status_code == 400


def test_dashboard_notes_endpoints_no_longer_exist(client):
    # GET falls through to the app's root StaticFiles mount (app/main.py),
    # which 404s on a path with no matching file -- confirming no FastAPI
    # route claims this path anymore. POST isn't asserted against the same
    # 404: StaticFiles rejects EVERY non-GET/HEAD method with 405 regardless
    # of path, which is a property of the catch-all mount, not evidence
    # either way about whether a /dashboard/notes route exists.
    assert client.get("/dashboard/notes").status_code == 404
