# Known issues

Documented, actively-scoped gaps -- not chased to a fix the moment they're
found, the same discipline `sim/KNOWN_ISSUES.md` uses on the engine side.

## Tick loop blocks the event loop under concurrent HTTP load

**Symptom:** a burst of concurrent requests (e.g. `/account`,
`/portfolio/attribution`) can fail or hang. In a browser this shows up
misleadingly as a CORS error in the console -- it isn't a CORS
misconfiguration, it's a stalled/reset connection being reported that way.

**Root cause:** `app/main.py`'s `_tick_loop` runs every `MARKET_TICK_SECONDS`
(1s) and calls `registry.step_all()` synchronously on the asyncio event loop
thread. `MarketRegistry`/`SymbolMarket` (`app/markets.py`) wraps
`Engine.submit`, a **blocking** IPC round-trip to the `simserver` Go
subprocess, called once per bot action per symbol per tick with no
`asyncio.to_thread` or executor offload. `run_strategies_once` /
`run_algo_orders_once` / `monitor_brackets` / `run_circuit_breakers_once` /
`refresh_pair_telemetry_once` are all plain synchronous calls in the same
loop. Under load this starves the event loop's ability to service other
requests.

**Reproduced:** 20 concurrent `curl` requests to `GET /account` produced
5x HTTP 500 and the rest hung until killed at 2 minutes. The server
recovers cleanly seconds later -- this is availability/latency under burst
load, not a crash, and not a correctness bug in any number the app
displays (the trading logic itself stays correctly serialized, exactly as
today, regardless of this fix).

**Ruled out:** SQLite's default rollback-journal mode was suspected first
(`app/db.py`'s own docstring assumes WAL but never actually issued
`PRAGMA journal_mode=WAL`). That pragma has since been added (`app/db.py`),
confirmed active, and does reduce risk under moderate concurrency -- but a
clean A/B (WAL mode is stored in the SQLite file header, so it stayed
active on disk even with the code-level pragma temporarily reverted) showed
the 20-concurrent hang is byte-for-byte identical with or without it.
SQLite locking is not the cause; keep the WAL pragma regardless, it's
correct and matches the module's stated intent, but don't expect it alone
to fix this.

**Why not fixed yet:** the obvious remedy -- wrap the tick loop's
synchronous work in `asyncio.to_thread` -- is not safe as a one-line
change. `internal/book`'s `Book` (see the root `README.md`) is deliberately
**not concurrency-safe**: one `Book` per goroutine, fed from a sequenced
stream. `routers/orders.py` already calls `market.eng.submit` synchronously
from request handlers today, currently serialized only because everything
runs on a single event-loop thread. Moving `step_all()` to a background
thread would introduce genuine concurrent access to the same
`MarketRegistry`/`Engine` objects from two threads -- a real race
condition in the order book, likely worse than the current availability
issue. A correct fix needs a single dedicated worker (thread or process)
that owns all engine access, with both the tick loop and HTTP order
submission dispatching through it -- not a change to make casually under
a bug-fix commit.

**How to apply:** don't add `asyncio.to_thread` around `registry.step_all()`
or any `market.eng.submit` call without first funneling *all* engine access
(tick loop AND request handlers) through one single-owner dispatch point.
