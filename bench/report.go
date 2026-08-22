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

	dir := filepath.Join("..", "results")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir results: %v", err)
	}
	f, err := os.Create(filepath.Join(dir, "latency.json"))
	if err != nil {
		t.Fatalf("create results/latency.json: %v", err)
	}
	defer f.Close()

	enc := json.NewEncoder(f)
	enc.SetIndent("", "  ")
	if err := enc.Encode(rep); err != nil {
		t.Fatalf("encode report: %v", err)
	}
}
