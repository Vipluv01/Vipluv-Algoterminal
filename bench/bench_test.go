// Package bench measures the matching engine under realistic order flow.
//
// These are Go benchmarks (`go test -bench`), not ad-hoc timing code, so
// aggregate throughput gets the language's own statistical tooling for free:
// -benchtime controls sample count and -benchmem reports allocations per
// operation directly, both amortized over enough iterations that per-call
// timer overhead washes out.
//
// Per-operation LATENCY PERCENTILES are a different problem. A direct
// time.Now()-before/time.Now()-after measurement around a single ~150ns
// operation runs into the timer's own resolution: on this machine, back-to-
// back time.Now() calls return an identical timestamp 96.8% of the time (see
// bench/timercheck), so naively timing individual ops would report a "p50 of
// 0ns" that is pure measurement artifact, not signal. Publishing that number
// would be worse than not measuring at all.
//
// The fix is the standard one for sub-microsecond operations: time small
// batches of same-kind operations together and attribute the batch's average
// to each member. This trades exact per-op attribution (which the clock
// cannot give here regardless) for a percentile distribution that reflects
// real variation in book state and operation cost across batches, without
// being dominated by clock quantization.
package bench

import (
	"testing"

	"github.com/vipluv/bourse/internal/book"
	"github.com/vipluv/bourse/internal/workload"
)

func newBook(b testing.TB, capacity int) *book.Book {
	b.Helper()
	bk, err := book.New(book.Config{MinPx: 1, MaxPx: 200_000, Tick: 1, Capacity: capacity})
	if err != nil {
		b.Fatalf("New: %v", err)
	}
	return bk
}

// BenchmarkThroughput reports raw ops/sec and allocations under sustained
// realistic order flow -- the single number the README leads with. This is
// unaffected by the timer-resolution issue above: testing.B's timer wraps
// the entire b.N-iteration loop once, not each operation individually, so
// call overhead is amortized to negligible.
func BenchmarkThroughput(b *testing.B) {
	bk := newBook(b, 1<<20)
	gen := workload.New(workload.DefaultParams(100_000), 1)

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		op := gen.Next()
		switch op.Kind {
		case workload.OpSubmit:
			fills, reject := bk.Submit(op.Order)
			if reject == book.RejectNone && len(fills) == 0 {
				gen.Track(op.Order.ID)
			}
		case workload.OpCancel:
			bk.Cancel(op.CancelID)
		}
	}
}
