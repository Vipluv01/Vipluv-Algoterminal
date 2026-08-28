package bench

import (
	"runtime"
	"time"

	"github.com/vipluv/bourse/internal/book"
	"github.com/vipluv/bourse/internal/workload"
)

// throughputResult is the measured steady-state cost of sustained mixed order
// flow, plus the allocation behaviour that goes with it.
//
// Allocations are counted with runtime.ReadMemStats rather than read off
// `-benchmem`, for a specific reason: -benchmem reports allocs/op as an
// integer, so anything below one allocation per operation prints as a flat
// "0 allocs/op". That rounding is how a README ends up claiming "zero heap
// allocations" when the true figure is small-but-nonzero. Mallocs is an exact
// counter, so AllocsPerOp below is a real ratio and can be reported honestly.
type throughputResult struct {
	Ops          int     `json:"n_operations"`
	Submits      int     `json:"n_submits"`
	Cancels      int     `json:"n_cancels"`
	ElapsedNs    int64   `json:"elapsed_ns"`
	NsPerOp      float64 `json:"ns_per_op"`
	OpsPerSecond float64 `json:"ops_per_second"`
	BytesPerOp   float64 `json:"bytes_per_op"`
	AllocsPerOp  float64 `json:"allocs_per_op"`
	TotalMallocs uint64  `json:"total_mallocs"`
	// LiveOrders is the book's resting-order count when the measurement
	// finished. It is reported because throughput is NOT independent of it --
	// see the sweep in TestThroughputReport.
	LiveOrders int `json:"live_orders_at_end"`
	Capacity   int `json:"capacity"`
	// Saturated is true when the arena filled during the run, meaning some
	// submits were rejected with RejectBookFull rather than doing real work.
	// A saturated run does not measure throughput, it measures rejection, and
	// must never be published as a throughput figure.
	Saturated bool `json:"saturated"`
}

// runThroughput measures sustained aggregate throughput on a warmed book.
//
// Unlike runLatency this does NOT batch or restructure the stream: ops are
// applied in exactly the order the generator produces them, and one wall-clock
// reading wraps the whole measured run. At millions of operations the timer's
// ~32ns call overhead is amortized to nothing, so the batching workaround that
// per-operation percentiles require is unnecessary here -- this is the one
// measurement the clock can take directly.
//
// The book is warmed first for the same reason runLatency warms it: throughput
// against an empty book measures inserts into empty price levels, which is not
// the steady state a running exchange is ever in.
func runThroughput(totalOps int, seed int64, capacity int) throughputResult {
	bk, _ := book.New(book.Config{MinPx: 1, MaxPx: 200_000, Tick: 1, Capacity: capacity})
	gen := workload.New(workload.DefaultParams(100_000), seed)

	warmup := totalOps / 10
	for i := 0; i < warmup; i++ {
		applyOp(bk, gen, gen.Next())
	}

	// Ops are drawn and applied inline, NOT pre-generated into a slice.
	//
	// That is deliberate and was a real bug here first: the generator only
	// emits a cancel for an order it is currently tracking, and orders enter
	// its tracking set via gen.Track() inside applyOp. Draining Next() into a
	// slice without applying anything therefore starves the cancel path --
	// the first version of this function produced 43,500 cancels out of 4.5M
	// ops (1%) instead of the ~42% DefaultParams specifies, and reported a
	// correspondingly fictional 16M ops/sec because it was measuring an
	// almost pure-submit stream.
	//
	// The consequence is that the generator's own per-op cost (RNG draws,
	// Laplace price sampling, Pareto size sampling) lands inside the measured
	// window and cannot be factored out, because the stream's content depends
	// on what has already been applied. This figure is therefore a
	// conservative floor on engine throughput -- the engine alone is faster
	// than what is reported here. It is measured the same way
	// BenchmarkThroughput measures it, so the two are directly comparable.
	measured := totalOps - warmup
	submits, cancels := 0, 0

	runtime.GC()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)

	t0 := time.Now()
	for i := 0; i < measured; i++ {
		op := gen.Next()
		if op.Kind == workload.OpSubmit {
			submits++
		} else {
			cancels++
		}
		applyOp(bk, gen, op)
	}
	elapsed := time.Since(t0)

	runtime.ReadMemStats(&after)

	n := float64(measured)
	mallocs := after.Mallocs - before.Mallocs
	bytes := after.TotalAlloc - before.TotalAlloc
	live := bk.Stats().LiveOrders

	return throughputResult{
		Ops:          measured,
		Submits:      submits,
		Cancels:      cancels,
		ElapsedNs:    elapsed.Nanoseconds(),
		NsPerOp:      float64(elapsed.Nanoseconds()) / n,
		OpsPerSecond: n / elapsed.Seconds(),
		BytesPerOp:   float64(bytes) / n,
		AllocsPerOp:  float64(mallocs) / n,
		TotalMallocs: mallocs,
		LiveOrders:   live,
		Capacity:     capacity,
		Saturated:    live >= capacity,
	}
}
