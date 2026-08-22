package bench

import (
	"fmt"
	"testing"
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

// TestLatencyReport is the same measurement as a Test (not gated behind
// -bench) so `go test ./bench/...` always regenerates results/latency.json,
// keeping the README's numbers reproducible with one command.
func TestLatencyReport(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping full latency report in -short mode")
	}
	const n = 2_000_000
	res := runLatency(n, 1)

	fmt.Println()
	fmt.Println("=== bourse latency report (n =", n, "realistic ops, batch-timed) ===")
	fmt.Println("submit :", res.submit.Summarize())
	fmt.Println("cancel :", res.cancel.Summarize())
	fmt.Println("sweep  :", res.sweep.Summarize())

	writeReport(t, res)
}
