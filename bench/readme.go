package bench

import (
	"fmt"
	"strings"
)

// Markers delimiting the generated region of README.md. Everything between
// them is produced by renderResultsBlock and must never be edited by hand --
// TestReadmeMatchesMeasuredResults fails the build if it is.
const (
	resultsBegin = "<!-- BEGIN GENERATED RESULTS -->"
	resultsEnd   = "<!-- END GENERATED RESULTS -->"
)

// renderResultsBlock produces the README's entire Results section from the
// measured artifacts.
//
// The section is generated rather than written because the previous version
// was written, and it was wrong: it published latency figures that did not
// match results/latency.json while claiming they were read from it, and a
// throughput headline with no artifact behind it at all. Generating the block
// makes that class of drift impossible rather than merely discouraged.
func renderResultsBlock(lat Report, thr ThroughputReport, stab StabilityReport) string {
	var b strings.Builder

	b.WriteString(resultsBegin)
	b.WriteString("\n\n")

	fmt.Fprintf(&b, "Measured on an Apple M5 (10-core). Every figure below is generated from\n")
	fmt.Fprintf(&b, "`results/latency.json`, `results/throughput.json`, and\n")
	fmt.Fprintf(&b, "`results/latency_stability.json` by `go test ./bench/...` — this section of\n")
	fmt.Fprintf(&b, "the README is written by the benchmark, not by hand, and a test fails the\n")
	fmt.Fprintf(&b, "build if the two disagree.\n\n")

	// --- Throughput ---
	h := thr.Headline
	fmt.Fprintf(&b, "### Throughput\n\n")
	fmt.Fprintf(&b, "**%.2fM ops/sec** sustained (%.1f ns/op) under realistic mixed order flow\n",
		h.OpsPerSecond/1e6, h.NsPerOp)
	fmt.Fprintf(&b, "across 200 synthetic participants — %s submits and %s cancels, measured\n",
		commasf(int64(h.Submits)), commasf(int64(h.Cancels)))
	fmt.Fprintf(&b, "against a book holding **%s resting orders**.\n\n", commasf(int64(h.LiveOrders)))

	fmt.Fprintf(&b, "That book depth is quoted deliberately: throughput here is not a single\n")
	fmt.Fprintf(&b, "number. Cost per operation climbs as resting orders accumulate, so an\n")
	fmt.Fprintf(&b, "unqualified ops/sec figure is only true of whichever run length produced\n")
	fmt.Fprintf(&b, "it. The full curve:\n\n")

	fmt.Fprintf(&b, "| operations | resting orders | ns/op | ops/sec |\n")
	fmt.Fprintf(&b, "|---|---|---|---|\n")
	for _, s := range thr.Scaling {
		fmt.Fprintf(&b, "| %s | %s | %.1f | %.2fM |\n",
			commasf(int64(s.Ops)), commasf(int64(s.LiveOrders)), s.NsPerOp, s.OpsPerSecond/1e6)
	}
	fmt.Fprintf(&b, "\nThe headline is the last row — the deepest book measured, and so the most\n")
	fmt.Fprintf(&b, "conservative of them.\n\n")

	fmt.Fprintf(&b, "**Allocations: %d total across %s operations** (%.2f B/op amortized). This is\n",
		h.TotalMallocs, commasf(int64(h.Ops)), h.BytesPerOp)
	fmt.Fprintf(&b, "counted with `runtime.ReadMemStats`, not read off `-benchmem`, because\n")
	fmt.Fprintf(&b, "`-benchmem` reports allocs/op as an integer and prints anything below one\n")
	fmt.Fprintf(&b, "per operation as a flat `0 allocs/op` — which is how a README ends up\n")
	fmt.Fprintf(&b, "claiming a hard zero. The real number is small and non-zero: a handful of\n")
	fmt.Fprintf(&b, "buffer growths, amortized to effectively nothing, but not literally none.\n\n")

	// --- Latency ---
	fmt.Fprintf(&b, "### Latency\n\n")
	fmt.Fprintf(&b, "Nanoseconds, by operation type, batch-timed (see *Why the numbers can be\n")
	fmt.Fprintf(&b, "trusted* below for why batching is necessary here):\n\n")
	fmt.Fprintf(&b, "| | p50 | p90 | p99 | p99.9 | p99.99 | max |\n")
	fmt.Fprintf(&b, "|---|---|---|---|---|---|---|\n")
	b.WriteString(latencyRowFor("submit", lat.Submit) + "\n")
	b.WriteString(latencyRowFor("cancel", lat.Cancel) + "\n")
	b.WriteString(latencyRowFor("sweep", lat.Sweep) + "\n")
	fmt.Fprintf(&b, "\n*sweep = an aggressive order crossing 3+ price levels.*\n\n")

	// --- Reproducibility ---
	fmt.Fprintf(&b, "### Which of these numbers actually reproduce\n\n")
	fmt.Fprintf(&b, "Not all of them, and the difference matters more than the values.\n")
	fmt.Fprintf(&b, "Across **%d independent runs** of %s operations each, here is how far each\n",
		stab.Reps, commasf(int64(stab.OpsPerRep)))
	fmt.Fprintf(&b, "statistic moved (max ÷ min):\n\n")

	fmt.Fprintf(&b, "| | p50 | p90 | p99 | p99.9 | p99.99 | max |\n")
	fmt.Fprintf(&b, "|---|---|---|---|---|---|---|\n")
	b.WriteString(stabilityRowFor("submit", stab.Submit) + "\n")
	b.WriteString(stabilityRowFor("cancel", stab.Cancel) + "\n")
	b.WriteString(stabilityRowFor("sweep", stab.Sweep) + "\n")

	worstBody := maxRatio(
		stab.Submit.P50, stab.Cancel.P50, stab.Sweep.P50,
		stab.Submit.P90, stab.Cancel.P90, stab.Sweep.P90,
		stab.Submit.P99, stab.Cancel.P99, stab.Sweep.P99,
	)
	worstTail := maxRatio(
		stab.Submit.P999, stab.Cancel.P999, stab.Sweep.P999,
		stab.Submit.P9999, stab.Cancel.P9999, stab.Sweep.P9999,
		stab.Submit.Max, stab.Cancel.Max, stab.Sweep.Max,
	)

	fmt.Fprintf(&b, "\n**Up to p99 these reproduce; past it they do not.** Every statistic through\n")
	fmt.Fprintf(&b, "p99 stays within %.2fx of itself across runs — median submit cost lands in\n", worstBody)
	fmt.Fprintf(&b, "%s–%sns and sweep in %s–%sns, tight enough to quote as figures. Past p99 the\n",
		commasf(stab.Submit.P50.Min), commasf(stab.Submit.P50.Max),
		commasf(stab.Sweep.P50.Min), commasf(stab.Sweep.P50.Max))
	fmt.Fprintf(&b, "spread widens to %.2fx, which is more than enough to reverse a ranking built\n", worstTail)
	fmt.Fprintf(&b, "on it.\n\n")

	fmt.Fprintf(&b, "This is worth stating plainly because an earlier version of this README did\n")
	fmt.Fprintf(&b, "build such a ranking — it explained at length why submit's relative tail was\n")
	fmt.Fprintf(&b, "worse than sweep's and attributed the cause to the self-trade-prevention\n")
	fmt.Fprintf(&b, "path. That comparison also depends on which tail statistic you pick: by\n")
	fmt.Fprintf(&b, "p99.99 submit's tail is the larger multiple of its own median, but by `max`\n")
	fmt.Fprintf(&b, "sweep's is, by a wide margin. A single run cannot distinguish a real\n")
	fmt.Fprintf(&b, "structural difference from sampling noise at these percentiles, so the\n")
	fmt.Fprintf(&b, "explanation has been withdrawn rather than restated. What the tail does\n")
	fmt.Fprintf(&b, "support: cancel is consistently the cheapest operation at the median (a pure\n")
	fmt.Fprintf(&b, "O(1) unlink, no matching), and sweep's worst case is consistently the\n")
	fmt.Fprintf(&b, "largest absolute outlier, which is what you would expect from an operation\n")
	fmt.Fprintf(&b, "whose cost scales with how many levels it happens to cross.\n\n")

	b.WriteString(resultsEnd)
	return b.String()
}

// maxRatio returns the widest max/min spread among the given statistics.
func maxRatio(ss ...Spread) float64 {
	worst := 0.0
	for _, s := range ss {
		if s.RatioMaxMin > worst {
			worst = s.RatioMaxMin
		}
	}
	return worst
}

func latencyRowFor(label string, s summaryLike) string {
	return fmt.Sprintf("| **%s** | %s | %s | %s | %s | %s | %s |",
		label,
		commasf(s.P50Ns), commasf(s.P90Ns), commasf(s.P99Ns),
		commasf(s.P999Ns), commasf(s.P9999Ns), commasf(s.MaxNs))
}

func stabilityRowFor(label string, o OpStability) string {
	return fmt.Sprintf("| **%s** | %.2fx | %.2fx | %.2fx | %.2fx | %.2fx | %.2fx |",
		label,
		o.P50.RatioMaxMin, o.P90.RatioMaxMin, o.P99.RatioMaxMin,
		o.P999.RatioMaxMin, o.P9999.RatioMaxMin, o.Max.RatioMaxMin)
}

// summaryLike mirrors the fields of hdr.Summary this file needs, so the
// renderer does not depend on the histogram package's full surface.
type summaryLike = struct {
	Count   int64   `json:"count"`
	MinNs   int64   `json:"min_ns"`
	MeanNs  float64 `json:"mean_ns"`
	P50Ns   int64   `json:"p50_ns"`
	P90Ns   int64   `json:"p90_ns"`
	P99Ns   int64   `json:"p99_ns"`
	P999Ns  int64   `json:"p999_ns"`
	P9999Ns int64   `json:"p9999_ns"`
	MaxNs   int64   `json:"max_ns"`
}

// commasf formats an integer with thousands separators.
func commasf(n int64) string {
	s := fmt.Sprintf("%d", n)
	neg := strings.HasPrefix(s, "-")
	if neg {
		s = s[1:]
	}
	if len(s) > 3 {
		var b strings.Builder
		lead := len(s) % 3
		if lead > 0 {
			b.WriteString(s[:lead])
		}
		for i := lead; i < len(s); i += 3 {
			if b.Len() > 0 {
				b.WriteByte(',')
			}
			b.WriteString(s[i : i+3])
		}
		s = b.String()
	}
	if neg {
		return "-" + s
	}
	return s
}
