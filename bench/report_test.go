package bench

import (
	"fmt"
	"testing"
	"time"
)

// BenchmarkLatencyPercentiles runs one long realistic session and prints
// full percentile breakdowns per operation type.
func BenchmarkLatencyPercentiles(b *testing.B) {
	const n = 2_000_000
	res := runLatency(n, 1)

	b.Logf("\n--- submit (rest/immediate-fill, non-sweep) ---\n%s", res.submit.Summarize())
	b.Logf("--- cancel ---\n%s", res.cancel.Summarize())
	b.Logf("--- sweep (crosses 3+ levels, qty > 2000) ---\n%s", res.sweep.Summarize())
}

// TestLatencyReport regenerates results/latency.json.
//
// Opt-in via BOURSE_REGEN=1 (see skipUnlessRegen): an ordinary test run
// verifies the committed artifacts rather than overwriting them.
func TestLatencyReport(t *testing.T) {
	skipUnlessRegen(t)
	res := runLatency(latencyReportOps, 1)
	n := latencyReportOps

	fmt.Println()
	fmt.Println("=== bourse latency report (n =", n, "realistic ops, batch-timed) ===")
	fmt.Println("submit :", res.submit.Summarize())
	fmt.Println("cancel :", res.cancel.Summarize())
	fmt.Println("sweep  :", res.sweep.Summarize())

	writeReport(t, res)
}

// TestThroughputReport regenerates results/throughput.json, the counterpart to
// results/latency.json. Before it existed the README's headline ops/sec figure
// was the one published number with no artifact behind it at all.
func TestThroughputReport(t *testing.T) {
	skipUnlessRegen(t)

	// Measured at several run lengths rather than one, because throughput
	// here is a function of book depth, not a constant. The workload's 42%
	// cancel rate does not fully offset its submit rate, so resting orders
	// accumulate and per-op cost climbs with them. A single number would be
	// true only of whichever run length happened to produce it.
	sizes := []int{1_000_000, 5_000_000, 10_000_000, 30_000_000}

	// Capacity is sized so the arena never fills. An earlier version of this
	// test ran at 1<<20 and its largest row ended with live orders equal to
	// capacity exactly -- the book was full, so a share of those "operations"
	// were RejectBookFull early-outs rather than matching work. That row was
	// measuring rejection cost, not throughput. Saturated runs are now
	// detected and refused outright rather than quietly published.
	const capacity = 1 << 23

	fmt.Println()
	fmt.Println("=== bourse throughput report ===")
	fmt.Printf("%12s  %10s  %10s  %12s  %12s\n", "ops", "ns/op", "M ops/sec", "live orders", "allocs/op")

	var scaling []throughputResult
	for _, n := range sizes {
		res := runThroughput(n, 1, capacity)
		if res.Saturated {
			t.Fatalf("run of %d ops saturated the book (%d live orders at capacity %d): "+
				"this measures RejectBookFull, not throughput -- raise capacity",
				n, res.LiveOrders, res.Capacity)
		}
		scaling = append(scaling, res)
		fmt.Printf("%12d  %10.1f  %10.2f  %12d  %12.6f\n",
			res.Ops, res.NsPerOp, res.OpsPerSecond/1e6, res.LiveOrders, res.AllocsPerOp)
	}

	// The largest unsaturated run is the headline: the most conservative
	// figure, measured against the deepest book reached.
	headline := scaling[len(scaling)-1]
	fmt.Printf("\nheadline: %.2fM ops/sec at %d resting orders (%d total mallocs across %d ops)\n",
		headline.OpsPerSecond/1e6, headline.LiveOrders, headline.TotalMallocs, headline.Ops)

	writeThroughputReport(t, headline, scaling)
}

// TestLatencyStabilityReport measures the benchmark's own reproducibility.
//
// The engine's percentiles are only meaningful if they survive being measured
// again. This runs the full latency measurement repeatedly and records how far
// each statistic moved, which is what lets the README publish p50/p90 as
// figures while publishing the extreme tail as a range.
func TestLatencyStabilityReport(t *testing.T) {
	skipUnlessRegen(t)

	// opsPerRep MUST match the headline report's op count. Latency here is a
	// function of book depth, and depth is a function of how many ops have
	// run -- measuring stability at a different op count would produce ranges
	// that do not bracket the figures actually published in the table.
	const reps = 5
	const opsPerRep = latencyReportOps

	submit, cancel, sweep := runLatencyStability(reps, opsPerRep)

	fmt.Println()
	fmt.Printf("=== latency stability across %d runs of %d ops (max/min per statistic) ===\n", reps, opsPerRep)
	for _, row := range []struct {
		label string
		o     OpStability
	}{{"submit", submit}, {"cancel", cancel}, {"sweep", sweep}} {
		fmt.Printf("%-7s p50=%.2fx p90=%.2fx p99=%.2fx p99.9=%.2fx p99.99=%.2fx max=%.2fx\n",
			row.label, row.o.P50.RatioMaxMin, row.o.P90.RatioMaxMin, row.o.P99.RatioMaxMin,
			row.o.P999.RatioMaxMin, row.o.P9999.RatioMaxMin, row.o.Max.RatioMaxMin)
	}

	writeStabilityReport(t, StabilityReport{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Reps:        reps,
		OpsPerRep:   opsPerRep,
		Submit:      submit,
		Cancel:      cancel,
		Sweep:       sweep,
	})
}
