"""app/broker/notify.py -- fire-and-forget Telegram notifications. Every
test here waits for the spawned daemon thread to finish (join with a
short timeout) so assertions run after the send actually happened,
without ever calling _send_sync directly -- these are testing notify()'s
real threading behavior, not bypassing it."""

from __future__ import annotations

import threading
import time

import pytest

from app.broker import notify as notify_module


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def _run_and_wait(fn, *args, **kwargs):
    before = {t.ident for t in threading.enumerate()}
    fn(*args, **kwargs)
    # notify() starts a daemon thread and returns immediately -- give it a
    # moment to actually run and finish before asserting on its effects.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        new_threads = [t for t in threading.enumerate() if t.ident not in before]
        if not any(t.is_alive() for t in new_threads):
            break
        time.sleep(0.01)


def test_notify_is_a_noop_without_bot_token_or_chat_id(monkeypatch):
    calls = []
    monkeypatch.setattr("httpx.post", lambda *a, **k: calls.append((a, k)))
    _run_and_wait(notify_module.notify, "hello")
    assert calls == []


def test_notify_posts_to_telegram_when_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    calls = []
    monkeypatch.setattr("httpx.post", lambda *a, **k: calls.append((a, k)))

    _run_and_wait(notify_module.notify, "hello world")

    assert len(calls) == 1
    (url,), kwargs = calls[0]
    assert url == "https://api.telegram.org/bottest-token/sendMessage"
    assert kwargs["json"] == {"chat_id": "12345", "text": "hello world"}
    assert kwargs["timeout"] == notify_module._TIMEOUT_SECONDS


def test_notify_swallows_a_network_failure_without_raising(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def _boom(*a, **k):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr("httpx.post", _boom)
    # The whole point: this must not raise, and notify() itself returns
    # immediately regardless of what the background thread does.
    _run_and_wait(notify_module.notify, "hello")


def test_notify_returns_immediately_even_when_the_send_would_be_slow(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def _slow_post(*a, **k):
        time.sleep(1.0)

    monkeypatch.setattr("httpx.post", _slow_post)
    start = time.monotonic()
    notify_module.notify("hello")
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, "notify() must return immediately, not block on the HTTP call"


def test_notify_order_submitted_and_circuit_breaker_trip_build_readable_messages(monkeypatch):
    calls = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr("httpx.post", lambda *a, **k: calls.append(k["json"]["text"]))

    _run_and_wait(notify_module.notify_order_submitted, symbol="RELIANCE", side="buy", qty=5, broker_order_id="X1")
    _run_and_wait(notify_module.notify_circuit_breaker_trip, user_id=7)

    assert any("RELIANCE" in c and "X1" in c for c in calls)
    assert any("7" in c for c in calls)
