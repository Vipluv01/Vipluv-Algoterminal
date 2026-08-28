package bench

import (
	"math/rand"
	"time"

	"github.com/vipluv/bourse/internal/book"
	"github.com/vipluv/bourse/internal/hdr"
	"github.com/vipluv/bourse/internal/workload"
)

// batchSize is chosen so a batch's wall time comfortably clears timer
// overhead: at ~150ns/op, 32 ops is ~5us, two orders of magnitude above the
// ~32ns per-call overhead measured for time.Now(), so quantization noise
// contributes well under 1% of the measured batch duration.
const batchSize = 32

// latencyReportOps is the op count behind every published latency figure.
// The headline report and the stability sweep share it deliberately: per-op
// cost rises with book depth, so ranges measured at a different op count would
// not bracket the numbers in the published table.
const latencyReportOps = 2_000_000

type latencyResult struct {
	submit *hdr.Histogram
	cancel *hdr.Histogram
	sweep  *hdr.Histogram // book-walking fills (3+ levels): kept separate
	                       // because it has fundamentally different cost
	                       // than a single-level touch
}

// runLatency populates a book with realistic resting flow, then measures
// batched, same-kind operations against that live state.
//
// Warm-up first, then measure: an empty book is not representative -- cancel
// cost depends on list position, submit cost depends on whether it rests or
// matches, and both depend on the book actually having realistic depth. The
// warm-up phase runs ordinary interleaved flow (like BenchmarkThroughput) to
// reach that state; only the measurement phase is restructured into
// same-kind batches, and purely for timing-resolution reasons -- it does not
// change what operations run or in what economic scenario, only how their
// cost is attributed to the histogram.
func runLatency(totalOps int, seed int64) latencyResult {
	bk, _ := book.New(book.Config{MinPx: 1, MaxPx: 200_000, Tick: 1, Capacity: 1 << 20})
	gen := workload.New(workload.DefaultParams(100_000), seed)
	rng := rand.New(rand.NewSource(seed))

	res := latencyResult{submit: hdr.New(), cancel: hdr.New(), sweep: hdr.New()}

	warmup := totalOps / 10
	for i := 0; i < warmup; i++ {
		applyOp(bk, gen, gen.Next())
	}

	// Batches are explicitly rotated across the three kinds rather than
	// waiting for the natural stream to happen to produce `batchSize`
	// consecutive ops of one kind. Sweeps are ~3% of real flow, so passively
	// waiting for 32 in a row would starve that histogram entirely (which is
	// exactly what an earlier version of this function did). Rotation
	// weights approximate real proportions: cancels are ~42% of steps,
	// sweeps deliberately over-sampled relative to their 3% natural rate so
	// the tail histogram has enough mass to report p99.9/p99.99 meaningfully
	// -- sweep COST is measured on realistic-sized crossing orders either way,
	// only the SAMPLING rate is boosted.
	rotation := []batchKind{
		batchSubmit, batchSubmit, batchCancel, batchSubmit, batchCancel,
		batchSweep, batchSubmit, batchCancel, batchSubmit, batchSweep,
	}

	remaining := totalOps - warmup
	i := 0
	for remaining > 0 {
		target := rotation[i%len(rotation)]
		i++

		var ops []workload.Op
		if target == batchSweep {
			ops = forcedSweepBatch(gen, rng, batchSize)
		} else {
			ops = drainToTarget(bk, gen, target, batchSize)
		}
		if len(ops) == 0 {
			continue
		}

		t0 := time.Now()
		for _, op := range ops {
			applyOp(bk, gen, op)
		}
		dt := time.Since(t0)
		perOp := dt.Nanoseconds() / int64(len(ops))

		hist := res.submit
		switch target {
		case batchCancel:
			hist = res.cancel
		case batchSweep:
			hist = res.sweep
		}
		for range ops {
			hist.Record(perOp)
		}

		remaining -= len(ops)
	}

	return res
}

type batchKind uint8

const (
	batchSubmit batchKind = iota
	batchCancel
	batchSweep
)

// drainToTarget pulls from the generator's ordinary realistic stream until
// `n` operations of `target` kind have been collected, applying (off the
// clock) any operation of a different kind encountered along the way so
// nothing is skipped or reordered relative to the rest of the stream -- only
// dropped from timing attribution for this particular batch.
func drainToTarget(bk *book.Book, gen *workload.Generator, target batchKind, n int) []workload.Op {
	var batch []workload.Op
	for tries := 0; tries < n*50 && len(batch) < n; tries++ {
		op := gen.Next()
		k := batchCancel
		if op.Kind == workload.OpSubmit {
			k = batchSubmit
		}
		if k != target {
			applyOp(bk, gen, op)
			continue
		}
		batch = append(batch, op)
	}
	return batch
}

// forcedSweepBatch synthesizes n deliberately oversized crossing orders that
// walk 3+ price levels. Prices and the alternating side still come from the
// generator's own clustered model -- only the QUANTITY is forced above the
// sweep threshold, which is what "sweep" means (a large aggressive order),
// so this measures the real cost of that operation rather than a synthetic
// stand-in for it.
func forcedSweepBatch(gen *workload.Generator, rng *rand.Rand, n int) []workload.Op {
	batch := make([]workload.Op, 0, n)
	for i := 0; i < n; i++ {
		base := gen.Next()
		if base.Kind != workload.OpSubmit {
			i--
			continue
		}
		base.Order.Qty = book.Qty(3000 + rng.Intn(4000)) // reliably crosses 3+ levels
		base.Order.Type = book.MarketOrder                // guarantees it walks the book rather than resting
		batch = append(batch, base)
	}
	return batch
}

func applyOp(bk *book.Book, gen *workload.Generator, op workload.Op) {
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
