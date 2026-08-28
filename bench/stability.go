package bench

import (
	"sort"

	"github.com/vipluv/bourse/internal/hdr"
)

// Spread is the observed range of one statistic across repeated independent
// measurement runs.
type Spread struct {
	Min    int64 `json:"min"`
	Median int64 `json:"median"`
	Max    int64 `json:"max"`
	// RatioMaxMin is Max/Min. A value near 1.0 means the statistic reproduces;
	// a large value means it does not, and any narrative built on it is
	// describing one run rather than the engine.
	RatioMaxMin float64 `json:"ratio_max_min"`
}

// OpStability holds per-percentile spreads for a single operation kind.
type OpStability struct {
	P50   Spread `json:"p50"`
	P90   Spread `json:"p90"`
	P99   Spread `json:"p99"`
	P999  Spread `json:"p999"`
	P9999 Spread `json:"p9999"`
	Max   Spread `json:"max"`
}

// StabilityReport answers a question the benchmark could not previously
// answer about itself: which of its published numbers actually reproduce.
//
// This matters because the README's tail commentary was built on p99.99. In
// repeated runs on this machine that statistic moves by more than 2x, so a
// causal story told about it ("submit's tail is worse than sweep's because of
// the self-trade-prevention path") is not a finding about the engine -- it is
// a finding about one run. Percentiles that do reproduce (p50, p90) are safe
// to publish as figures; the rest are published as measured ranges instead.
type StabilityReport struct {
	GeneratedAt string      `json:"generated_at"`
	Reps        int         `json:"reps"`
	OpsPerRep   int         `json:"ops_per_rep"`
	Submit      OpStability `json:"submit"`
	Cancel      OpStability `json:"cancel"`
	Sweep       OpStability `json:"sweep"`
}

// runLatencyStability repeats the full latency measurement `reps` times with a
// different seed each time and reports how much each statistic moved.
func runLatencyStability(reps, opsPerRep int) (submit, cancel, sweep OpStability) {
	subs := make([]hdr.Summary, 0, reps)
	cans := make([]hdr.Summary, 0, reps)
	swps := make([]hdr.Summary, 0, reps)

	for r := 0; r < reps; r++ {
		// A fresh seed per rep: repeating the identical stream would measure
		// only timer noise, not the run-to-run variation a reader of the
		// README would actually see when they regenerate these numbers.
		res := runLatency(opsPerRep, int64(r+1))
		subs = append(subs, res.submit.Summarize())
		cans = append(cans, res.cancel.Summarize())
		swps = append(swps, res.sweep.Summarize())
	}

	return summarize(subs), summarize(cans), summarize(swps)
}

func summarize(runs []hdr.Summary) OpStability {
	return OpStability{
		P50:   spreadOf(runs, func(s hdr.Summary) int64 { return s.P50Ns }),
		P90:   spreadOf(runs, func(s hdr.Summary) int64 { return s.P90Ns }),
		P99:   spreadOf(runs, func(s hdr.Summary) int64 { return s.P99Ns }),
		P999:  spreadOf(runs, func(s hdr.Summary) int64 { return s.P999Ns }),
		P9999: spreadOf(runs, func(s hdr.Summary) int64 { return s.P9999Ns }),
		Max:   spreadOf(runs, func(s hdr.Summary) int64 { return s.MaxNs }),
	}
}

func spreadOf(runs []hdr.Summary, pick func(hdr.Summary) int64) Spread {
	vals := make([]int64, len(runs))
	for i, r := range runs {
		vals[i] = pick(r)
	}
	sort.Slice(vals, func(i, j int) bool { return vals[i] < vals[j] })

	lo, hi := vals[0], vals[len(vals)-1]
	med := vals[len(vals)/2]
	ratio := 0.0
	if lo > 0 {
		ratio = float64(hi) / float64(lo)
	}
	return Spread{Min: lo, Median: med, Max: hi, RatioMaxMin: ratio}
}
