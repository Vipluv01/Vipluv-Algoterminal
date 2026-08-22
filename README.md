# bourse

A deterministic limit order book / matching engine, written from scratch in Go.

No framework, no external matching library — the price-time priority queue,
the arena allocator, the bitmap index, and the benchmark harness are all
implementation here. Built to answer two questions properly: *is it correct*,
and *is it fast* — in that order, because the second is meaningless without
the first.

## Results

Measured on an Apple M5 (10-core), `go test ./bench/... -bench . -benchmem`.

**Throughput:** ~5.5M ops/sec sustained, under realistic mixed order flow
across 200 distinct synthetic participants (limit, market, stop-limit, and
cancel — see [Workload](#workload) below), **zero heap allocations per
operation** on the hot path.

**Latency**, broken out by operation type (nanoseconds):

| | p50 | p90 | p99 | p99.9 | p99.99 | max |
|---|---|---|---|---|---|---|
| **submit** | 72 | 84 | 109 | 276 | 2,720 | 4,899 |
| **cancel** | 19 | 23 | 31 | 80 | 230 | 351 |
| **sweep** (crosses 3+ levels) | 89 | 119 | 144 | 312 | 1,080 | 17,518 |

Regenerate with `go test ./bench/... -run TestLatencyReport -v`, which also
rewrites `results/latency.json` — the numbers above are read from that file,
not typed in by hand, so they can't drift out of sync with what the code
actually measures.

**Read past p99 before trusting these.** Cancel is consistently the
cheapest operation (a pure O(1) unlink, no matching involved) — roughly 4x
faster than submit at the median, same shape as before self-trade
prevention was added. What changed is which operation has the worse
*relative* tail: **submit's p99.99 is now ~38x its own median**, while
sweep's is ~12x — the reverse of an earlier measurement of this engine.
The likely cause: a submit that turns out to collide with a same-owner
resting order now takes the self-trade-prevention path (cancel the resting
order, then keep walking) instead of a plain match, and that extra
book-mutation work is exactly the kind of rare, structurally different case
that shows up in a tail rather than the average. Sweep's tail, by contrast,
is dominated by how many levels a given walk happens to cross, which is a
smoother, more evenly-distributed cost — hence the flatter relative tail.
This is exactly the sort of thing a percentile breakdown is for: a single
mean would have hidden it completely.

## Why the numbers can be trusted

The first version of this benchmark reported a p50 of *0 nanoseconds* for
every operation. That number was wrong, and the way it was wrong is worth
stating explicitly: individual operations here cost 100-300ns, and on this
machine, back-to-back calls to `time.Now()` return an **identical timestamp
96.8% of the time** (measured directly — see the methodology note in
[`bench/latency.go`](bench/latency.go)). The clock's own resolution was
coarser than the thing being measured, so most samples were quantized to
zero. That is a benchmark measuring its own timer, not the engine.

The fix: operations are timed in small batches of the same kind (32 ops),
and the batch's wall-clock duration is divided across its members. At ~150ns
per operation, a 32-op batch takes ~5µs — two orders of magnitude above
`time.Now()`'s ~32ns call overhead, so quantization noise is now a rounding
error rather than the entire signal. This is a standard technique for
sub-microsecond benchmarking, not a shortcut; the alternative (trusting the
original numbers) would have been the wrong call.

## What's implemented

- **Order types**: limit, market, and stop-limit, including trigger
  cascades (one stop firing can trigger another, bounded to prevent runaway
  chains)
- **Time-in-force**: GTC (rests on the book), IOC (fills what it can,
  cancels the rest), FOK (fills completely or not at all)
- **Self-trade prevention** (cancel-oldest mode): an order never matches
  against a resting order from the same owner — the resting order is
  cancelled outright and the incoming order continues walking the level as
  if it had never been there. Adding this surfaced two real bugs, both
  fixed and covered by dedicated tests (`internal/book/stp_test.go`): every
  existing test that used a default zero `Owner` on both sides of a trade
  suddenly failed, because two zero-owner orders now looked like the same
  participant — the fix was giving tests realistic distinct owners, not
  special-casing owner zero as exempt (that would make the safety feature
  silently ineffective exactly when someone forgets to set it). Second, and
  more interesting: an FOK order whose only reachable liquidity was its own
  resting order used to be *accepted* with zero fills and nothing resting —
  neither filled per FOK's own contract nor rejected, just silently gone —
  because `available()` (the FOK pre-check) summed level totals without
  knowing about ownership. Fixed by making it walk individual orders and
  exclude the taker's own.
- **Pre-trade risk checks**, both opt-in via `Config` (zero disables each):
  - **Price collar** (fat-finger protection) — rejects a limit order priced
    more than `PriceCollarBps` from the last traded price. Deliberately
    scoped to limit orders: a market order's price is an internal
    implementation detail (re-priced to the band edge to reuse one matching
    path), and collaring that would reject nearly every market order for
    the wrong reason.
  - **Position limits** — rejects an order outright if it could push the
    owner's *net filled* position beyond `PositionLimit`, checked
    pessimistically against the order's full size whether it rests or
    fills immediately. This has a real, deliberate scope boundary worth
    stating plainly: it checks realized position, not open-order exposure,
    so three separate smaller resting orders from the same owner are each
    individually admitted even if their *combined* size would exceed the
    limit (`TestPositionLimitDoesNotAccumulateAcrossSeparateRestingOrders`
    demonstrates this concretely, not just in a comment) — a genuine
    circumvention path for anyone who chops one large order into many, not
    a bug.
  - Adding position tracking enabled a new, cheap, powerful invariant:
    every fill moves quantity between exactly two parties in opposite
    directions, so the sum of *every* owner's position across the whole
    market must always be exactly zero. `Check()` verifies this after every
    operation in the property tests (100k random ops), which is also strong
    evidence the position-tracking implementation itself is correct — a
    single wrong-side or double-counted fill anywhere would show up
    immediately as a nonzero sum.
- **O(1) cancellation** regardless of book depth, via an intrusive doubly
  linked list per price level plus a handle index
- **Integer tick prices**, never floats — floating point in a matching
  engine is a correctness bug: two orders that should cross sometimes won't,
  silently
- **Arena allocation with integer handles** instead of pointers, so Go's
  garbage collector never has to walk the order book — the usual cause of
  tail-latency blowups in GC'd languages under load
- **Three-level hierarchical bitmap** for O(1) best-price lookup, so finding
  the best bid or ask costs the same three word-reads whether the book is
  dense or has one order sitting far from the touch

## How correctness is verified

Speed is worthless if the book is wrong, so this was built in the opposite
order: an [invariant checker](internal/book/invariants.go) came first,
verifying ~15 structural properties — the book never crosses, price-time
(FIFO) ordering holds within every level, cached depth totals match the sum
of resting orders, the handle index and the arena agree on what's live.

```
go test ./...                          # full suite, ~45s (includes 100k-op property tests)
go test ./internal/book/ -run Property  # random order flow, invariants checked after EVERY operation
go test ./internal/book/ -run Deterministic
go test ./internal/book/ -fuzz FuzzBook -fuzztime 30s
```

**The invariant checker found a real bug within an hour of being written.**
`Cancel` removed an order from its price level's linked list but never
decremented the level's cached total quantity — so cancelling an order
silently *inflated* the depth the book reported at that price. No crash, no
failing type check, no obvious symptom: a market data consumer would have
seen phantom liquidity. That's the class of bug that surfaces weeks later as
a reconciliation break nobody can explain, and it only got caught here
because correctness was checked exhaustively before performance was measured
at all. Fixed in [`internal/book/book.go`](internal/book/book.go) — `unlink`
is now the single owner of level-total arithmetic, on both the fill path and
the cancel path.

Also verified: **deterministic replay**. The same sequence of orders run
through two independent book instances produces byte-identical fills, every
time (`TestDeterministicReplay`, 50,000 ops → 25,000+ fills, compared
element-by-element). This is what would make crash recovery via event-log
replay possible, and what makes a disputed trade reconstructible after the
fact — a property real exchanges depend on and this one preserves by
construction (no wall-clock reads, no map iteration, no goroutine
concurrency inside the book itself).

## Workload

Benchmarking against uniformly random orders would be indefensible — no real
market looks like that. [`internal/workload`](internal/workload/generator.go)
generates flow with three properties real order books actually have:
prices cluster near the current touch rather than spreading evenly across
the price band (Laplace-distributed offset, not uniform); order sizes follow
a heavy right tail (Pareto, α=1.6 — mostly small, occasionally large); and a
large fraction of activity is cancellation (~42%), because that's what
resting limit orders mostly do in a real market. Orders are also spread
across a pool of 200 distinct synthetic owners — needed once self-trade
prevention shipped, since leaving every order at the zero-value owner would
have made the whole benchmark look like one participant trading against
itself, with STP cancelling most crossing orders instead of matching them.
The generator is itself seeded and deterministic, so a regression can be
distinguished from workload noise between runs.

## Crash recovery

[`internal/wal`](internal/wal/README.md) — a write-ahead log built entirely
on the determinism proof above: log every *accepted* operation's inputs,
replay them into a fresh book from empty, and the result is provably
identical to the live book (not approximately — a 5,000-op randomized
session test checks full price-level identity, not just summary stats).

The honest, measured finding there is worth reading: fsync costs **~15,000x**
the buffered write it guards, meaning per-entry-fsync durability sustains
roughly 290 writes/sec against this engine's ~5.5M ops/sec — a real,
quantified tradeoff between "never lose an acknowledged order" and
throughput, not a hand-wave.

**Wired into `cmd/simserver`, not just a standalone package**: pass
`wal_path` in `new_book`'s config and every accepted submit/cancel is
logged there; starting a new process against the same path replays it
automatically before accepting new orders, reporting how many entries it
recovered. `TestCrashRecoveryThroughTheWireProtocol` proves this against a
REAL crash, not a cooperative shutdown: it starts an actual subprocess,
submits orders, `SIGKILL`s it mid-session, starts a second independent
process pointed at the same log, and confirms the recovered book matches
exactly — correct best bid, correct depth (a cancelled order correctly
excluded), passing invariants, and the recovered book continuing to match
new incoming orders normally, not just serving as a read-only snapshot.

## Market simulation (`sim/`)

Everything above is the matching engine in isolation. On top of it,
[`sim/`](sim/) is an agent-based market simulation (`bourse_sim`, Python)
that drives the *same* engine through `cmd/simserver`'s JSON protocol —
`NoiseTrader`, `InformedTrader`, and a `MarketMaker` (also an
Avellaneda-Stoikov variant) submit real orders against a real book, so the
traded price **emerges** from that order flow rather than being assigned by
a formula. `demo_server.py` runs the same primitives live over WebSocket for
a browser demo ([`sim/demo/`](sim/demo/), see `sim/demo/README.md`).

**That claim is checked, not just asserted.** `stylized_facts.py` validates
the simulated price series against three well-documented empirical
regularities of real markets (Cont 2001): fat tails, weak return
autocorrelation, and volatility clustering. The negative control makes the
point directly — pure GBM (the original, discarded design, which *assigns*
price via a formula) fails all three by construction, which is exactly what
`test_pure_gbm_shows_none_of_the_stylized_facts` checks.

Regenerate the persisted, citable result with:

```bash
cd sim && .venv/bin/python scripts/generate_stylized_facts_report.py
```

which writes [`results/stylized_facts.json`](results/stylized_facts.json)
from 5 independent seeds of the default single-maker pipeline, the same
discipline `results/latency.json` uses on the Go side. The current, honest
result: **fat tails and weak return autocorrelation pass on 5/5 seeds;
volatility clustering fails on 5/5**, with the wrong sign. That third one is
not unexamined noise — it's a real, actively-investigated open finding, with
several hypotheses already ruled out by direct measurement (book staleness,
inventory-skew overcorrection) and a best-supported explanation reached
(one dominant market maker re-centering the touch every refresh acts like
bid-ask bounce at the level of the maker's own quotes). Read
[`sim/KNOWN_ISSUES.md`](sim/KNOWN_ISSUES.md) before assuming this is a bug
or re-litigating a hypothesis it already ruled out.

## Explicitly not built

Real exchanges have these; this project deliberately doesn't, and each
omission is a choice rather than an oversight:

- **A network gateway** (FIX or a binary protocol) — plumbing that would
  teach little the matching core doesn't already demonstrate.
- **Multi-instrument sharding** — one book per core with a routing layer.

## Layout

```
internal/book/       matching engine core — types, arena, bitmap index,
                      matching logic, stop-order triggers, invariant checker
internal/hdr/         high-dynamic-range latency histogram (own
                      implementation — see file header for why a plain
                      fixed-width histogram doesn't work at this range)
internal/workload/    realistic, seeded, deterministic order-flow generator
bench/                benchmark harness: throughput, batch-timed latency
                      percentiles, JSON report generation
cmd/simserver/        newline-delimited JSON protocol over stdin/stdout --
                      the seam the Python simulation drives the engine through
sim/                  agent-based market simulation (Python, bourse_sim package):
                      agents, stylized-facts validation, live WebSocket demo
                      (see sim/KNOWN_ISSUES.md for an honest open finding)
results/latency.json          regenerated by `go test ./bench/... -run TestLatencyReport`
results/stylized_facts.json   regenerated by `sim/scripts/generate_stylized_facts_report.py`
```
