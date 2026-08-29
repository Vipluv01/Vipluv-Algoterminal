"""app/routers/live_market.py's live_market_ws -- the router-level wiring
to app/broker/feed_registry.py (acquire on connect, release on
disconnect, a throttled creation closes cleanly rather than being retried
by this endpoint itself). feed_registry's own sharing/throttling logic is
tested directly in test_feed_registry.py; these tests replace
acquire_feed/release_feed with fakes to isolate the ROUTER's own wiring.
"""

from __future__ import annotations

from app.broker.feed_registry import FeedCreationThrottled


class _StubAdapter:
    def resolve_equity_symbol(self, exchange, symbol):
        return {"exchange": "NSE", "tradingsymbol": f"{symbol}-EQ", "symboltoken": "2885"}

    def ensure_session(self):
        pass


def test_live_ws_acquires_and_releases_the_feed_around_the_connection(client, monkeypatch):
    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: _StubAdapter())

    acquired, released = {}, {}
    monkeypatch.setattr("app.routers.live_market.acquire_feed", lambda **kw: acquired.update(kw))
    monkeypatch.setattr("app.routers.live_market.release_feed", lambda **kw: released.update(kw))

    with client.websocket_connect("/live/ws/market/RELIANCE"):
        assert acquired["symbol"] == "RELIANCE"
        assert acquired["symboltoken"] == "2885"
        assert callable(acquired["on_tick"])
        assert not released, "must not release before the connection actually closes"

    assert released["symbol"] == "RELIANCE"
    assert released["subscriber_id"] is acquired["subscriber_id"], (
        "release must be called with the SAME subscriber_id acquire was given, "
        "or feed_registry can never match them up to know this subscriber left"
    )


def test_live_ws_closes_cleanly_when_feed_creation_is_throttled_not_retried(client, monkeypatch):
    monkeypatch.setattr("app.routers.live_market.get_adapter_for_user", lambda db, user_id: _StubAdapter())

    def _throttled(**kwargs):
        raise FeedCreationThrottled("a live feed for 'RELIANCE' was created too recently -- try again in 7.3s")

    monkeypatch.setattr("app.routers.live_market.acquire_feed", _throttled)
    released = {}
    monkeypatch.setattr("app.routers.live_market.release_feed", lambda **kw: released.update(kw))

    try:
        with client.websocket_connect("/live/ws/market/RELIANCE"):
            pass
        assert False, "expected the connection to be rejected"
    except Exception:
        pass  # starlette's test client raises on a closed-during-handshake websocket, same as test_market_ws.py

    assert not released, "a throttled acquire never subscribed anything -- there is nothing to release"
