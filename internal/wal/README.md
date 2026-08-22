# Write-ahead log

Crash recovery for `internal/book`, built entirely on a property that
package already proves: `TestDeterministicReplay` shows the same operation
sequence produces byte-identical results across independent `Book`
instances. That means recovery doesn't need snapshots or checksummed
pages — logging every *accepted* operation's inputs and replaying them into
a fresh book from empty is sufficient and correct by construction.

## What's proven

`TestReplayReconstructsIdenticalStateAfterRandomizedSession` drives a live
book through 5,000 randomized operations (3,668 of them accepted and
logged), then replays the log into an independent fresh book and asserts
full identity: trade count, volume, live-order count, best bid/ask, and
**every resting price level** match exactly between the live book and the
recovered one. Not "close" — identical.

## The real, measured cost of durability

```
BenchmarkWriterLogSubmitWithFsync-10                864     3,452,229 ns/op
BenchmarkWriterLogSubmitNoFsyncForComparisonOnly    9002136       226.6 ns/op
```

fsync is **~15,000x** the cost of the buffered write it guards. At one
fsync per accepted operation, this WAL sustains roughly 290 writes/sec —
against a matching engine measured at ~5.5M ops/sec (see the top-level
README). Used synchronously in the hot path, the WAL would be the system's
actual bottleneck by four orders of magnitude, not the book.

This is a deliberate choice, not an oversight: the current design is
correctness-first — nothing is ever acknowledged and then lost to a crash,
full stop, no exceptions. That is the right default for a component whose
entire purpose is not losing data. A production system that needed higher
sustained throughput would group-commit instead (batch N entries, or flush
every T milliseconds, fsync once per batch) — trading a bounded window of
possible loss (whatever was written since the last flush) for throughput
much closer to the buffered-write number above. That tradeoff is explicit
future work, not implemented here, and the honest number above is what
makes it possible to reason about instead of guess at.

## What this is not

- **Not a snapshot mechanism.** Recovery always replays from the beginning
  of the log. For a long-lived book this means recovery time grows with
  log length — the standard fix (periodic snapshot + only-replay-since)
  is a pure optimization on top of what's here, never a correctness
  requirement, since replay-from-empty is already exactly correct.
**Now wired into `cmd/simserver`**: pass `wal_path` in `new_book`'s config
and every accepted submit/cancel is logged there automatically; a fresh
process pointed at the same path replays it before accepting new orders.
Proved against a real crash, not a cooperative shutdown — see
`cmd/simserver/main_test.go`'s `TestCrashRecoveryThroughTheWireProtocol`,
which `SIGKILL`s a live subprocess mid-session and confirms an independent
second process recovers to an identical, still-functional book.
