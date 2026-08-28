package bench

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/vipluv/bourse/internal/hdr"
)

type Report struct {
	GeneratedAt string      `json:"generated_at"`
	N           int         `json:"n_operations"`
	BatchSize   int         `json:"timing_batch_size"`
	Submit      hdr.Summary `json:"submit"`
	Cancel      hdr.Summary `json:"cancel"`
	Sweep       hdr.Summary `json:"sweep"`
}

// ThroughputReport is the persisted form of a runThroughput measurement.
//
// This file exists because the README's headline throughput figure previously
// had no backing artifact at all: latency.json was generated and cited, but
// ops/sec was only ever a number in `go test -bench` stdout, so nothing stopped
// it from drifting away from what the engine actually does. Now both halves of
// the Results section are generated from files on disk.
type ThroughputReport struct {
	GeneratedAt string `json:"generated_at"`
	// Headline is the figure the README quotes. It is one row of Scaling,
	// named explicitly so the published number is always tied to the book
	// state it was measured at rather than floating free.
	Headline throughputResult   `json:"headline"`
	Scaling  []throughputResult `json:"scaling"`
	Note     string             `json:"note"`
}

// regenEnv gates the artifact-generating tests.
//
// Generation is deliberately NOT part of an ordinary `go test ./...` run.
// When it was, the suite invalidated itself: every run rewrote results/*.json
// with fresh measurements, so the very next run failed the README guard
// because the committed prose no longer matched the newly-measured numbers.
// A suite that fails every second run trains people to ignore it, which is
// precisely how the original benchmark drift went unnoticed.
//
// So the split is: `go test ./...` VERIFIES the committed artifacts against
// the committed README (fast, deterministic, writes nothing), and
// regeneration is an explicit act:
//
//	BOURSE_REGEN=1 go test ./bench/... -run Report
const regenEnv = "BOURSE_REGEN"

// skipUnlessRegen skips an artifact-generating test unless regeneration was
// explicitly requested.
func skipUnlessRegen(t *testing.T) {
	t.Helper()
	if os.Getenv(regenEnv) != "1" {
		t.Skipf("artifact generation is opt-in; run with %s=1 to regenerate results/", regenEnv)
	}
}

// writeReport persists measured percentiles to results/latency.json at the
// repo root -- the README's numbers are generated FROM this file, not typed
// in by hand, so they cannot drift out of sync with what the code measures.
func writeReport(t *testing.T, res latencyResult) {
	t.Helper()

	rep := Report{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		N:           2_000_000,
		BatchSize:   batchSize,
		Submit:      res.submit.Summarize(),
		Cancel:      res.cancel.Summarize(),
		Sweep:       res.sweep.Summarize(),
	}

	writeJSON(t, "latency.json", rep)
}

// writeThroughputReport persists measured ops/sec and per-operation allocation
// counts to results/throughput.json, the companion to latency.json.
func writeThroughputReport(t *testing.T, headline throughputResult, scaling []throughputResult) {
	t.Helper()

	writeJSON(t, "throughput.json", ThroughputReport{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Headline:    headline,
		Scaling:     scaling,
		Note: "Throughput is reported against a stated book depth because it is " +
			"not constant: cost per operation rises as resting-order count grows. " +
			"Quoting a single unqualified ops/sec figure hides that. The headline " +
			"row is the largest measured run, i.e. the most conservative.",
	})
}

// writeStabilityReport persists how far each latency percentile moved across
// repeated runs -- the artifact behind the README's "which of these numbers
// actually reproduce" section.
func writeStabilityReport(t *testing.T, rep StabilityReport) {
	t.Helper()
	writeJSON(t, "latency_stability.json", rep)
}

func writeJSON(t *testing.T, name string, payload any) {
	t.Helper()

	dir := filepath.Join("..", "results")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir results: %v", err)
	}
	f, err := os.Create(filepath.Join(dir, name))
	if err != nil {
		t.Fatalf("create results/%s: %v", name, err)
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(payload); err != nil {
		t.Fatalf("encode %s: %v", name, err)
	}
}
