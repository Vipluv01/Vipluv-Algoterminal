"""Shared AngelOneLiveFeed per (user_id, symbol) -- the fix for a real
incident, not a preemptive optimization. Before this existed,
routers/live_market.py's WS endpoint constructed a brand-new
AngelOneLiveFeed (a real WebSocket connection to Angel One) for EVERY
inbound browser connection, with no reuse and no server-side limit on how
often that could happen. Confirmed live (Aug 28-29): an uncapped
client-side retry loop reconnecting repeatedly, combined with that
per-connection feed creation, produced 1,539 "resubscribe/reconnect"
attempts against the real account over ~4 hours, hitting Angel One's own
connection-rate limiter (429 "Connection Limit Exceeded") repeatedly. The
client-side retry loop is being capped separately (frontend); this is the
server-side half -- and it stands on its own regardless of whether the
client behaves, which is the actual lesson of the incident: a single
misbehaving client (a bug, a stuck tab, several tabs) should never be able
to hammer a real external rate limit with no backstop at the layer that
actually owns the upstream connection.

Two independent protections:
  1. Sharing: multiple subscribers to the same (user_id, symbol) get ONE
     real upstream connection, fanned out to all of them -- a chart and a
     ticker strip both showing RELIANCE no longer means two real
     connections.
  2. Throttling: even a genuinely NEW feed (the first subscriber for a
     key, or the first one after the previous subscriber count dropped to
     zero and the feed was torn down) cannot be created more often than
     MIN_CREATE_INTERVAL_SECONDS for the same key -- a real ceiling
     independent of the client's own retry behavior.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from app.broker.angelone import AngelOneAdapter, AngelOneLiveFeed

log = logging.getLogger(__name__)

# Not a request rate limit -- subscribing to an already-open shared feed
# is free and instant. Specifically bounds how often a real NEW WebSocket
# handshake against Angel One can be opened for the same (user_id,
# symbol). 10s gives real headroom against a tight retry loop (the
# incident's storm was reconnecting many times per minute, not once every
# few seconds) without meaningfully delaying a genuine new subscriber --
# the previous feed's own teardown is not itself rate-limited, only
# re-creation is.
MIN_CREATE_INTERVAL_SECONDS = 10.0


class FeedCreationThrottled(Exception):
    pass


@dataclass
class _ManagedFeed:
    feed: AngelOneLiveFeed
    # subscriber_id -> that subscriber's own on_tick callback. A dict, not
    # a set, because fan-out needs to call each subscriber's OWN callback
    # (each bridges into a different WebSocket connection's own event
    # loop via its own call_soon_threadsafe closure) -- not one callback
    # shared across all of them.
    callbacks: dict[object, Callable[[dict], None]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


_lock = threading.Lock()
_feeds: dict[tuple[int, str], _ManagedFeed] = {}
_last_create_attempt: dict[tuple[int, str], float] = {}


def _fanout(key: tuple[int, str], data: dict) -> None:
    """Runs on the shared feed's own background thread (see
    AngelOneLiveFeed's own docstring) -- takes a lock only long enough to
    snapshot the current callback list, so a subscriber joining or
    leaving mid-fanout can't mutate the dict this is iterating, and a
    slow/failing subscriber callback can't hold the lock (and therefore
    every OTHER subscriber's delivery) hostage.
    """
    with _lock:
        managed = _feeds.get(key)
        callbacks = list(managed.callbacks.values()) if managed is not None else []
    for callback in callbacks:
        try:
            callback(data)
        except Exception:
            log.warning("live feed subscriber callback failed", exc_info=True)


def acquire_feed(
    *, user_id: int, symbol: str, adapter: AngelOneAdapter, symboltoken: str,
    subscriber_id: object, on_tick: Callable[[dict], None],
) -> None:
    """Registers `on_tick` as one subscriber of the shared feed for
    (user_id, symbol), creating that feed (a real Angel One connection)
    only if none currently exists. subscriber_id is an opaque per-caller
    identity (routers/live_market.py passes its own WebSocket instance) --
    release_feed below needs the SAME object to know which subscriber is
    leaving.

    Raises FeedCreationThrottled instead of creating a new connection when
    the previous one for this key was created too recently -- callers
    should surface this as a clear, temporary rejection (a WS close code),
    never retry it silently in a loop themselves.
    """
    key = (user_id, symbol)
    with _lock:
        managed = _feeds.get(key)
        if managed is not None:
            managed.callbacks[subscriber_id] = on_tick
            return

        now = time.monotonic()
        last_attempt = _last_create_attempt.get(key)
        if last_attempt is not None and (now - last_attempt) < MIN_CREATE_INTERVAL_SECONDS:
            wait = MIN_CREATE_INTERVAL_SECONDS - (now - last_attempt)
            raise FeedCreationThrottled(
                f"a live feed for {symbol!r} was created too recently -- try again in {wait:.1f}s"
            )
        _last_create_attempt[key] = now

        feed = AngelOneLiveFeed(
            auth_token=adapter._jwt_token, api_key=adapter._creds.api_key,
            client_code=adapter._creds.client_code, feed_token=adapter._feed_token,
        )
        managed = _ManagedFeed(feed=feed)
        managed.callbacks[subscriber_id] = on_tick
        _feeds[key] = managed

    # Real network I/O (a real WebSocket handshake) -- deliberately
    # outside the lock above, so one slow connect doesn't block every
    # other symbol's acquire_feed/release_feed call.
    feed.start(on_tick=lambda data: _fanout(key, data))
    feed.subscribe([symboltoken])


def release_feed(*, user_id: int, symbol: str, subscriber_id: object) -> None:
    """Called when one subscriber (a WS connection closing) goes away --
    stops and evicts the shared upstream feed only once EVERY subscriber
    for this key has gone, not on the first one to leave."""
    key = (user_id, symbol)
    with _lock:
        managed = _feeds.get(key)
        if managed is None:
            return
        managed.callbacks.pop(subscriber_id, None)
        if managed.callbacks:
            return
        del _feeds[key]
    managed.feed.stop()
