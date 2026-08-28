"""Fire-and-forget Telegram notifications (fills, circuit-breaker trips).

Explicit design constraint, stated directly in the Phase 7 brief: this
must NOT repeat webapp/KNOWN_ISSUES.md's tick-loop mistake (a synchronous,
unoffloaded call sitting in a hot path and stalling the whole event loop
under load). Callers here span both a sync FastAPI route (routers/orders.py's
live-order confirm endpoint, plain `def`) and an already-async context
(app/risk/circuit_breaker.py's check_circuit_breaker, called from
app/main.py's async _tick_loop) -- rather than two different
implementations (one asyncio-native, one thread-based) for those two call
shapes, notify() always spawns a plain daemon thread running a SYNCHRONOUS,
short-timeout HTTP call. That one implementation is correct from either
caller: a sync route calling it doesn't block on anything (the thread is
fired and the route returns immediately), and an async caller calling it
doesn't block the event loop either (spawning a thread is a cheap,
non-blocking call from asyncio's perspective). If Telegram is slow or
down, the timeout bounds how long that ONE background thread lives;
nothing else -- not the request, not the tick loop -- ever waits on it.

No-op (does nothing, raises nothing) when TELEGRAM_BOT_TOKEN or
TELEGRAM_CHAT_ID isn't set -- notifications are a nice-to-have, not a
dependency anything else here should ever be gated on.
"""

from __future__ import annotations

import logging
import os
import threading

import httpx

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5.0


def _send_sync(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception:
        # Never let a Telegram/network failure surface anywhere -- this
        # notification was never load-bearing for the caller's own work
        # (an order confirming, a circuit breaker tripping) to begin with.
        log.warning("Telegram notification failed to send", exc_info=True)


def notify(text: str) -> None:
    """Fire-and-forget: returns immediately, regardless of caller (sync
    route or async tick-loop context) -- see this module's own docstring
    for why a plain daemon thread is the one implementation that's
    correct from both."""
    threading.Thread(target=_send_sync, args=(text,), daemon=True).start()


def notify_order_submitted(*, symbol: str, side: str, qty: int, broker_order_id: str) -> None:
    # "Submitted", not "filled" -- this app has no live fill-status poller
    # (Angel One's order book would need to be polled, or a postback/
    # webhook wired up, to know a live order actually FILLED rather than
    # merely being accepted by the broker; out of this phase's scope).
    # Saying "filled" here would be a claim this code doesn't actually
    # have grounds for.
    notify(f"Live order submitted to broker: {side.upper()} {qty} {symbol} (broker order {broker_order_id})")


def notify_circuit_breaker_trip(*, user_id: int) -> None:
    notify(f"Circuit breaker tripped for user {user_id}: trading halted, positions being flattened.")
