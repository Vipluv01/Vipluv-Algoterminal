package hdr

import (
	"math"
	"math/rand"
	"sort"
	"testing"
)

// The core validation: percentiles read off the histogram must match
// percentiles computed by brute-force sorting the same raw samples, within
// the ~1.5% relative error the bucket structure promises. An HDR histogram
// that's subtly wrong still returns plausible-looking numbers -- this is the
// only check that catches that.
func TestPercentilesMatchBruteForce(t *testing.T) {
	rng := rand.New(rand.NewSource(42))
	const n = 200_000

	h := New()
	raw := make([]int64, n)
	for i := 0; i < n; i++ {
		// Log-normal: representative of real latency distributions, which
		// are right-skewed with a long tail, not symmetric.
		v := int64(math.Exp(rng.NormFloat64()*1.2+6) + 1)
		raw[i] = v
		h.Record(v)
	}
	sort.Slice(raw, func(i, j int) bool { return raw[i] < raw[j] })

	bruteForcePercentile := func(p float64) int64 {
		idx := int(math.Ceil((p / 100.0) * float64(n))) - 1
		if idx < 0 {
			idx = 0
		}
		if idx >= n {
			idx = n - 1
		}
		return raw[idx]
	}

	for _, p := range []float64{50, 90, 99, 99.9, 99.99} {
		got := h.Percentile(p)
		want := bruteForcePercentile(p)
		relErr := math.Abs(float64(got-want)) / float64(want)
		if relErr > 0.02 {
			t.Errorf("p%.2f: got %d want %d (rel err %.4f, exceeds 2%% bound)", p, got, want, relErr)
		}
	}

	if h.Count() != n {
		t.Errorf("count = %d, want %d", h.Count(), n)
	}
	if h.Min() > raw[0] {
		t.Errorf("min = %d, want <= %d", h.Min(), raw[0])
	}
	if h.Max() < raw[n-1] {
		t.Errorf("max = %d, want >= %d", h.Max(), raw[n-1])
	}
}

func TestMeanIsClose(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	const n = 100_000
	h := New()
	var sum float64
	for i := 0; i < n; i++ {
		v := int64(rng.ExpFloat64()*5000) + 1
		h.Record(v)
		sum += float64(v)
	}
	want := sum / n
	got := h.Mean()
	relErr := math.Abs(got-want) / want
	if relErr > 0.02 {
		t.Errorf("mean = %.1f, want ~%.1f (rel err %.4f)", got, want, relErr)
	}
}

func TestEmptyHistogram(t *testing.T) {
	h := New()
	if h.Count() != 0 {
		t.Error("empty histogram should have count 0")
	}
	if h.Percentile(50) != 0 {
		t.Error("empty histogram percentile should be 0, not panic or garbage")
	}
}

func TestSingleValue(t *testing.T) {
	h := New()
	h.Record(1500)
	for _, p := range []float64{1, 50, 99, 99.99} {
		got := h.Percentile(p)
		relErr := math.Abs(float64(got-1500)) / 1500
		if relErr > 0.02 {
			t.Errorf("single-value p%.2f = %d, want ~1500", p, got)
		}
	}
}

// A histogram used for a real benchmark must not require allocation per
// Record call -- that would make the measurement apparatus itself the
// dominant cost being measured.
func TestRecordAllocatesNothing(t *testing.T) {
	h := New()
	allocs := testing.AllocsPerRun(1000, func() {
		h.Record(12345)
	})
	if allocs != 0 {
		t.Errorf("Record allocates %.1f times per call, want 0", allocs)
	}
}

func BenchmarkRecord(b *testing.B) {
	h := New()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		h.Record(int64(i%1_000_000) + 1)
	}
}
