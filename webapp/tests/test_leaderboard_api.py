"""Leaderboard -- app/routers/leaderboard.py. Real P&L reconstructed from
SymbolMarket.recent_fills, not simulated for display."""

from datetime import datetime, timedelta, timezone

from app.main import app
from app.markets import HUMAN_USER_OWNER_ID


def _step(symbol: str, n: int = 1):
    market = app.state.registry.markets[symbol]
    for _ in range(n):
        market.step()
    return market


def test_leaderboard_lists_every_bot_plus_the_human_user(client):
    resp = client.get("/leaderboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 1 human + 1 market maker + 20 noise traders + 5 informed traders.
    assert len(body["entries"]) == 27
    owner_ids = {e["owner_id"] for e in body["entries"]}
    assert HUMAN_USER_OWNER_ID in owner_ids
    assert 1 in owner_ids  # market maker
    # Seed-liquidity owner (999) is explicitly excluded -- not a real trader.
    assert 999 not in owner_ids


def test_leaderboard_human_entry_is_labeled_you(client):
    body = client.get("/leaderboard").json()
    you = next(e for e in body["entries"] if e["owner_id"] == HUMAN_USER_OWNER_ID)
    assert you["label"] == "You"
    assert you["role"] == "human"


def test_leaderboard_entries_are_sorted_by_rank(client):
    body = client.get("/leaderboard").json()
    ranks = [e["rank"] for e in body["entries"]]
    assert ranks == sorted(ranks)


def test_leaderboard_with_no_since_has_null_deltas(client):
    body = client.get("/leaderboard").json()
    assert body["since"] is None
    assert all(e["pnl_delta"] is None and e["rank_delta"] is None for e in body["entries"])


def test_leaderboard_reflects_a_real_human_fill(client):
    before = {e["owner_id"]: e["pnl"] for e in client.get("/leaderboard").json()["entries"]}
    assert before[HUMAN_USER_OWNER_ID] == 0.0

    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})

    after = {e["owner_id"]: e["pnl"] for e in client.get("/leaderboard").json()["entries"]}
    # A market buy crossing the spread has a real, small, deterministic
    # mark-to-market cost the instant it fills (bought at the ask, marked
    # at the still-lower mid/last) -- not necessarily a loss forever, but
    # not exactly 0.0 either, proving this is wired to real fills.
    assert after[HUMAN_USER_OWNER_ID] != 0.0


def test_leaderboard_since_far_in_the_past_reports_full_pnl_as_the_delta(client):
    client.post("/orders", json={"symbol": "ICICIBANK", "side": "buy", "order_type": "market", "qty": 5})
    now_pnl = {e["owner_id"]: e["pnl"] for e in client.get("/leaderboard").json()["entries"]}

    since = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()
    body = client.get("/leaderboard", params={"since": since}).json()
    assert body["since"] is not None

    you = next(e for e in body["entries"] if e["owner_id"] == HUMAN_USER_OWNER_ID)
    # Cutoff clamps to step 0 (before any activity), so "since" covers the
    # account's entire retained history -- pnl_delta should equal the
    # full current pnl.
    assert you["pnl_delta"] == now_pnl[HUMAN_USER_OWNER_ID]


def test_leaderboard_since_now_reports_near_zero_delta(client):
    _step("ICICIBANK", 3)
    since = datetime.now(timezone.utc).isoformat()
    body = client.get("/leaderboard", params={"since": since}).json()
    # Essentially no time has passed since `since` -- every owner's
    # pnl_delta should be (near) zero, not their full accumulated pnl.
    for entry in body["entries"]:
        assert abs(entry["pnl_delta"]) < 1e-6


def test_leaderboard_since_within_retained_history_reports_coverage_complete(client):
    _step("ICICIBANK", 5)
    since = (datetime.now(timezone.utc) - timedelta(days=3650)).isoformat()
    body = client.get("/leaderboard", params={"since": since}).json()
    # Well under the 500-fill ring buffer capacity -- nothing evicted yet,
    # so even a far-past `since` is fully (if trivially) covered.
    assert body["since_coverage_complete"] is True


def test_leaderboard_fill_window_note_is_present(client):
    body = client.get("/leaderboard").json()
    assert body["fill_window_note"]
