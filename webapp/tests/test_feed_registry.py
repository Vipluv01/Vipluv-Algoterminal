"""app/broker/feed_registry.py -- the fix for a real incident (see its own
module docstring): a per-connection AngelOneLiveFeed, with no sharing and
no server-side creation limit, combined with an uncapped client-side
retry loop to produce 1,539 reconnect attempts against a real Angel One
account over ~4 hours. Every test here replaces AngelOneLiveFeed with a
fake (no real network, no real smartapi-python import) so these test the
registry's own sharing/throttling/fan-out logic in isolation.
"""

from __future__ import annotations

import pytest

from app.broker import feed_registry
from app.broker.angelone import AngelOneAdapter, AngelOneCredentials
from app.broker.feed_registry import FeedCreationThrottled, acquire_feed, release_feed


class _FakeLiveFeed:
    instances: list["_FakeLiveFeed"] = []

    def __init__(self, *, auth_token, api_key, client_code, feed_token):
        self.auth_token = auth_token
        self.started_on_tick = None
        self.subscribed_tokens = None
        self.stopped = False
        _FakeLiveFeed.instances.append(self)

    def start(self, *, on_tick, on_open=None):
        self.started_on_tick = on_tick

    def subscribe(self, tokens, correlation_id="algoterminal"):
        self.subscribed_tokens = tokens

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    # Module-level mutable state (_feeds, _last_create_attempt) must not
    # leak between tests -- this file is the only thing that touches it
    # directly, so a hard reset per test is simpler and safer than trying
    # to undo individual acquire/release calls.
    monkeypatch.setattr(feed_registry, "AngelOneLiveFeed", _FakeLiveFeed)
    feed_registry._feeds.clear()
    feed_registry._last_create_attempt.clear()
    _FakeLiveFeed.instances.clear()
    yield
    feed_registry._feeds.clear()
    feed_registry._last_create_attempt.clear()


def _adapter() -> AngelOneAdapter:
    creds = AngelOneCredentials(api_key="k", client_code="c", password="p", totp_secret="JBSWY3DPEHPK3PXP")
    adapter = AngelOneAdapter(creds)
    adapter._jwt_token = "jwt"
    adapter._feed_token = "feed"
    return adapter


def test_acquire_feed_creates_exactly_one_real_feed_for_two_subscribers(monkeypatch):
    adapter = _adapter()
    received_a, received_b = [], []

    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=received_a.append)
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-b", on_tick=received_b.append)

    assert len(_FakeLiveFeed.instances) == 1, "a second subscriber to the same symbol must reuse the existing feed"
    feed = _FakeLiveFeed.instances[0]
    assert feed.subscribed_tokens == ["2885"]

    # The fan-out registered with the fake feed must reach BOTH subscribers'
    # own callbacks, not just the one that happened to create the feed.
    feed.started_on_tick({"last_traded_price": 123})
    assert received_a == [{"last_traded_price": 123}]
    assert received_b == [{"last_traded_price": 123}]


def test_different_symbols_get_different_feeds(monkeypatch):
    adapter = _adapter()
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=lambda d: None)
    acquire_feed(user_id=1, symbol="TCS", adapter=adapter, symboltoken="11536",
                 subscriber_id="ws-b", on_tick=lambda d: None)
    assert len(_FakeLiveFeed.instances) == 2


def test_different_users_get_different_feeds_for_the_same_symbol(monkeypatch):
    adapter = _adapter()
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=lambda d: None)
    acquire_feed(user_id=2, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-b", on_tick=lambda d: None)
    assert len(_FakeLiveFeed.instances) == 2


def test_release_feed_only_tears_down_after_the_last_subscriber_leaves(monkeypatch):
    adapter = _adapter()
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=lambda d: None)
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-b", on_tick=lambda d: None)
    feed = _FakeLiveFeed.instances[0]

    release_feed(user_id=1, symbol="RELIANCE", subscriber_id="ws-a")
    assert not feed.stopped, "one remaining subscriber -- the shared feed must stay open"

    release_feed(user_id=1, symbol="RELIANCE", subscriber_id="ws-b")
    assert feed.stopped, "the last subscriber left -- the shared feed must be torn down"


def test_releasing_an_unknown_subscriber_is_a_safe_noop(monkeypatch):
    release_feed(user_id=999, symbol="NOTOPEN", subscriber_id="ghost")  # must not raise


def test_reacquiring_after_full_teardown_creates_a_fresh_feed(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(feed_registry, "MIN_CREATE_INTERVAL_SECONDS", 0.0)  # no throttle wait needed for this test

    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=lambda d: None)
    release_feed(user_id=1, symbol="RELIANCE", subscriber_id="ws-a")
    assert _FakeLiveFeed.instances[0].stopped

    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-c", on_tick=lambda d: None)
    assert len(_FakeLiveFeed.instances) == 2
    assert not _FakeLiveFeed.instances[1].stopped


def test_creating_a_new_feed_too_soon_after_the_last_one_is_throttled(monkeypatch):
    adapter = _adapter()
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=lambda d: None)
    release_feed(user_id=1, symbol="RELIANCE", subscriber_id="ws-a")
    assert _FakeLiveFeed.instances[0].stopped

    # MIN_CREATE_INTERVAL_SECONDS is real (10s) here -- immediately trying
    # to recreate the same key's feed must be refused, not silently open
    # a second real connection.
    with pytest.raises(FeedCreationThrottled):
        acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                     subscriber_id="ws-b", on_tick=lambda d: None)
    assert len(_FakeLiveFeed.instances) == 1, "a throttled acquire must not create a real feed"


def test_throttle_is_per_key_not_global(monkeypatch):
    adapter = _adapter()
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=lambda d: None)
    release_feed(user_id=1, symbol="RELIANCE", subscriber_id="ws-a")

    # A DIFFERENT symbol's feed creation must not be blocked by RELIANCE's
    # own recent creation -- the cooldown is scoped per (user_id, symbol).
    acquire_feed(user_id=1, symbol="TCS", adapter=adapter, symboltoken="11536",
                 subscriber_id="ws-b", on_tick=lambda d: None)
    assert len(_FakeLiveFeed.instances) == 2


def test_a_failing_subscriber_callback_does_not_block_delivery_to_others(monkeypatch):
    adapter = _adapter()
    received = []

    def _boom(data):
        raise RuntimeError("simulated subscriber failure")

    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-a", on_tick=_boom)
    acquire_feed(user_id=1, symbol="RELIANCE", adapter=adapter, symboltoken="2885",
                 subscriber_id="ws-b", on_tick=received.append)

    feed = _FakeLiveFeed.instances[0]
    feed.started_on_tick({"last_traded_price": 1})  # must not raise despite ws-a's callback failing
    assert received == [{"last_traded_price": 1}]
