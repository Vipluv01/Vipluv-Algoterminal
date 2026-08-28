package bench

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestReadmeMatchesMeasuredResults is the guard that makes the README's
// "Results" section trustworthy.
//
// This test exists because the claim was false. The README stated its latency
// table was "read from that file, not typed in by hand" while publishing
// submit p50 72ns / cancel 19ns / sweep 89ns against a results/latency.json
// that said 83 / 20 / 126 -- and its throughput headline had no backing file
// at all. The numbers were transcribed once and then drifted, silently, with
// nothing in the build to catch it.
//
// The fix is to stop transcribing: the section between the generated markers
// is produced by renderResultsBlock from the measured artifacts, and this test
// fails if README.md does not contain exactly that. Regenerate with:
//
//	go test ./bench/... -run 'Report'          # refresh the artifacts
//	BOURSE_UPDATE_README=1 go test ./bench/... -run Readme
func TestReadmeMatchesMeasuredResults(t *testing.T) {
	readmePath := filepath.Join("..", "README.md")
	readme := readFile(t, readmePath)

	var lat Report
	decodeJSON(t, filepath.Join("..", "results", "latency.json"), &lat)

	var thr ThroughputReport
	decodeJSON(t, filepath.Join("..", "results", "throughput.json"), &thr)

	var stab StabilityReport
	decodeJSON(t, filepath.Join("..", "results", "latency_stability.json"), &stab)

	want := renderResultsBlock(lat, thr, stab)

	start := strings.Index(readme, resultsBegin)
	end := strings.Index(readme, resultsEnd)
	if start < 0 || end < 0 {
		t.Fatalf("README.md is missing the generated-results markers %q / %q.\n"+
			"The Results section must be delimited by them so it can be generated "+
			"rather than hand-written.", resultsBegin, resultsEnd)
	}
	got := readme[start : end+len(resultsEnd)]

	if got == want {
		return
	}

	if os.Getenv("BOURSE_UPDATE_README") == "1" {
		updated := readme[:start] + want + readme[end+len(resultsEnd):]
		if err := os.WriteFile(readmePath, []byte(updated), 0o644); err != nil {
			t.Fatalf("update README.md: %v", err)
		}
		t.Log("README.md results section regenerated from measured artifacts")
		return
	}

	t.Errorf("README.md's Results section does not match the measured artifacts in results/.\n"+
		"This is the drift this test exists to catch -- the README is publishing numbers "+
		"the benchmark did not produce.\n\n"+
		"Regenerate with:\n"+
		"  BOURSE_UPDATE_README=1 go test ./bench/... -run Readme\n\n"+
		"--- README has ---\n%s\n\n--- artifacts say ---\n%s",
		firstLines(got, 24), firstLines(want, 24))
}

func firstLines(s string, n int) string {
	lines := strings.Split(s, "\n")
	if len(lines) <= n {
		return s
	}
	return strings.Join(lines[:n], "\n") + "\n  ... (truncated)"
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(b)
}

func decodeJSON(t *testing.T, path string, into any) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v\nregenerate the artifacts with: go test ./bench/... -run Report", path, err)
	}
	if err := json.Unmarshal(b, into); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}
