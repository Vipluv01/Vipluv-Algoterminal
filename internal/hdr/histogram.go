// Package hdr implements a compact high-dynamic-range latency histogram.
//
// Storing every raw sample and sorting at the end is the obvious approach and
// the wrong one here: a benchmark run easily produces tens of millions of
// samples, and keeping them all defeats the purpose of measuring something
// allocation-free. A plain fixed-width histogram is the standard fix, but it
// forces a choice between resolution at the low end (nanoseconds) and range at
// the high end (a GC pause can hit milliseconds) -- pick bins fine enough for
// the first and you don't have enough of them to reach the second.
//
// HDR histograms solve this by making bin width proportional to the value
// itself, which keeps relative error bounded (here, ~1.5%) across the entire
// range with a fixed, small number of bins. This is the same technique used
// by Gil Tene's HdrHistogram and by most production latency tooling; this
// package is a minimal implementation of the same idea, sized for one thing:
// recording matching-engine operation latencies in nanoseconds.
package hdr

import (
	"fmt"
	"math"
	"math/bits"
)

const (
	// sigFigs sets the guaranteed relative precision: 2 significant decimal
	// digits means any reported value is accurate to within ~1%, which is
	// tighter than the run-to-run noise this benchmark is trying to measure.
	sigFigs = 2

	minValue = 1        // 1ns floor -- operations faster than this don't happen
	maxValue = 60_000_000_000 // 60s ceiling -- generous headroom over any GC pause
)

// Histogram records non-negative int64 values (nanoseconds) and answers
// percentile queries over them in O(1) space relative to sample count.
type Histogram struct {
	unitMagnitude   int
	subBucketHalfCount int
	subBucketHalfCountMagnitude int
	subBucketCount  int
	subBucketMask   int64

	bucketCount int
	counts      []int64
	totalCount  int64
	minRecorded int64
	maxRecorded int64
}

func New() *Histogram {
	// Derived once at construction from sigFigs and the value range -- this
	// is standard HdrHistogram bucket math, not something to hand-tune per
	// use.
	largestValueWithSingleUnit := int64(math.Pow10(sigFigs) * 2)
	subBucketCountMagnitude := int(math.Ceil(math.Log2(float64(largestValueWithSingleUnit))))
	if subBucketCountMagnitude < 1 {
		subBucketCountMagnitude = 1
	}
	subBucketHalfCountMagnitude := subBucketCountMagnitude - 1
	subBucketCount := 1 << uint(subBucketCountMagnitude)
	subBucketHalfCount := subBucketCount / 2

	unitMagnitude := int(math.Floor(math.Log2(float64(minValue))))
	if minValue <= 0 {
		unitMagnitude = 0
	}

	subBucketMask := int64(subBucketCount-1) << uint(unitMagnitude)

	bucketsNeeded := 1
	smallestUntrackable := int64(subBucketCount) << uint(unitMagnitude)
	for smallestUntrackable < maxValue {
		smallestUntrackable <<= 1
		bucketsNeeded++
	}
	bucketCount := bucketsNeeded

	countsLen := (bucketCount + 1) * (subBucketCount / 2)

	return &Histogram{
		unitMagnitude:               unitMagnitude,
		subBucketHalfCount:          subBucketHalfCount,
		subBucketHalfCountMagnitude: subBucketHalfCountMagnitude,
		subBucketCount:              subBucketCount,
		subBucketMask:               subBucketMask,
		bucketCount:                 bucketCount,
		counts:                      make([]int64, countsLen),
		minRecorded:                 math.MaxInt64,
		maxRecorded:                 0,
	}
}

func (h *Histogram) Record(value int64) {
	if value < 0 {
		value = 0
	}
	idx := h.countsIndexFor(value)
	if idx >= 0 && idx < len(h.counts) {
		h.counts[idx]++
	} else {
		// Out of configured range: clamp into the top bucket rather than
		// panicking or silently dropping. A benchmark that hits this has a
		// tail worth knowing about, not a crash worth causing.
		h.counts[len(h.counts)-1]++
	}
	h.totalCount++
	if value < h.minRecorded {
		h.minRecorded = value
	}
	if value > h.maxRecorded {
		h.maxRecorded = value
	}
}

func (h *Histogram) bucketIndexFor(value int64) int {
	pow2Ceiling := 64 - bits.LeadingZeros64(uint64(value)|uint64(h.subBucketMask))
	return pow2Ceiling - h.unitMagnitude - (h.subBucketHalfCountMagnitude + 1)
}

func (h *Histogram) subBucketIndexFor(value int64, bucketIdx int) int {
	return int(value >> uint(bucketIdx+h.unitMagnitude))
}

func (h *Histogram) countsIndexFor(value int64) int {
	bucketIdx := h.bucketIndexFor(value)
	subBucketIdx := h.subBucketIndexFor(value, bucketIdx)
	bucketBaseIdx := (bucketIdx + 1) << uint(h.subBucketHalfCountMagnitude)
	offsetInBucket := subBucketIdx - h.subBucketHalfCount
	return bucketBaseIdx + offsetInBucket
}

func (h *Histogram) valueFromIndex(idx int) int64 {
	bucketIdx := (idx >> uint(h.subBucketHalfCountMagnitude)) - 1
	subBucketIdx := (idx & (h.subBucketHalfCount - 1)) + h.subBucketHalfCount
	if bucketIdx < 0 {
		subBucketIdx -= h.subBucketHalfCount
		bucketIdx = 0
	}
	return int64(subBucketIdx) << uint(bucketIdx+h.unitMagnitude)
}

// Percentile returns the value at or below which `p` percent of recorded
// samples fall. p is in (0, 100].
func (h *Histogram) Percentile(p float64) int64 {
	if h.totalCount == 0 {
		return 0
	}
	target := int64(math.Ceil((p / 100.0) * float64(h.totalCount)))
	var cumulative int64
	for i, c := range h.counts {
		cumulative += c
		if cumulative >= target {
			return h.valueFromIndex(i)
		}
	}
	return h.maxRecorded
}

func (h *Histogram) Min() int64 { return h.minRecorded }
func (h *Histogram) Max() int64 { return h.maxRecorded }
func (h *Histogram) Count() int64 { return h.totalCount }

func (h *Histogram) Mean() float64 {
	if h.totalCount == 0 {
		return 0
	}
	var sum float64
	for i, c := range h.counts {
		if c == 0 {
			continue
		}
		sum += float64(h.valueFromIndex(i)) * float64(c)
	}
	return sum / float64(h.totalCount)
}

// Summary is a snapshot suitable for printing or JSON encoding.
type Summary struct {
	Count      int64   `json:"count"`
	MinNs      int64   `json:"min_ns"`
	MeanNs     float64 `json:"mean_ns"`
	P50Ns      int64   `json:"p50_ns"`
	P90Ns      int64   `json:"p90_ns"`
	P99Ns      int64   `json:"p99_ns"`
	P999Ns     int64   `json:"p999_ns"`
	P9999Ns    int64   `json:"p9999_ns"`
	MaxNs      int64   `json:"max_ns"`
}

func (h *Histogram) Summarize() Summary {
	return Summary{
		Count:   h.totalCount,
		MinNs:   h.Min(),
		MeanNs:  h.Mean(),
		P50Ns:   h.Percentile(50),
		P90Ns:   h.Percentile(90),
		P99Ns:   h.Percentile(99),
		P999Ns:  h.Percentile(99.9),
		P9999Ns: h.Percentile(99.99),
		MaxNs:   h.Max(),
	}
}

func fmtNs(ns int64) string {
	switch {
	case ns < 1_000:
		return fmt.Sprintf("%dns", ns)
	case ns < 1_000_000:
		return fmt.Sprintf("%.2fµs", float64(ns)/1_000)
	case ns < 1_000_000_000:
		return fmt.Sprintf("%.2fms", float64(ns)/1_000_000)
	default:
		return fmt.Sprintf("%.2fs", float64(ns)/1_000_000_000)
	}
}

func (s Summary) String() string {
	return fmt.Sprintf(
		"n=%d  min=%s  mean=%s  p50=%s  p90=%s  p99=%s  p99.9=%s  p99.99=%s  max=%s",
		s.Count, fmtNs(s.MinNs), fmtNs(int64(s.MeanNs)), fmtNs(s.P50Ns), fmtNs(s.P90Ns),
		fmtNs(s.P99Ns), fmtNs(s.P999Ns), fmtNs(s.P9999Ns), fmtNs(s.MaxNs),
	)
}
