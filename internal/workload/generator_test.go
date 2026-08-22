package workload

import (
	"testing"

	"github.com/vipluv/bourse/internal/book"
)

// Guards against the generator silently degenerating -- e.g. a refactor that
// accidentally makes CancelProbability a no-op, or collapses price clustering
// to a single tick. Thresholds are loose on purpose: this checks "isn't
// broken," not "matches Params to the decimal."
func TestGeneratorIsNotDegenerate(t *testing.T) {
	p := DefaultParams(10000)
	g := New(p, 1)

	var submits, cancels, markets, stops int
	prices := map[book.Price]int{}
	var totalQty book.Qty

	const n = 50_000
	for i := 0; i < n; i++ {
		op := g.Next()
		if op.Kind == OpCancel {
			cancels++
			continue
		}
		submits++
		g.Track(op.Order.ID)
		switch op.Order.Type {
		case book.MarketOrder:
			markets++
		case book.StopLimitOrder:
			stops++
		}
		prices[op.Order.Px]++
		totalQty += op.Order.Qty
	}

	if submits == 0 || cancels == 0 {
		t.Fatalf("degenerate: submits=%d cancels=%d, want both > 0", submits, cancels)
	}
	cancelRate := float64(cancels) / float64(n)
	if cancelRate < 0.2 || cancelRate > 0.6 {
		t.Errorf("cancel rate %.2f is far from configured %.2f", cancelRate, p.CancelProbability)
	}
	if len(prices) < 20 {
		t.Errorf("only %d distinct prices generated -- clustering may have collapsed", len(prices))
	}
	if markets == 0 {
		t.Error("no market orders generated despite MarketOrderProbability > 0")
	}
	if stops == 0 {
		t.Error("no stop orders generated despite StopOrderProbability > 0")
	}
	avgQty := float64(totalQty) / float64(submits)
	if avgQty < 1 || avgQty > 500 {
		t.Errorf("average order size %.1f looks wrong for a Pareto(1.6) draw", avgQty)
	}
}

func TestGeneratorIsDeterministic(t *testing.T) {
	run := func(seed int64) []Op {
		g := New(DefaultParams(10000), seed)
		ops := make([]Op, 1000)
		for i := range ops {
			op := g.Next()
			if op.Kind == OpSubmit {
				g.Track(op.Order.ID)
			}
			ops[i] = op
		}
		return ops
	}

	a, b := run(42), run(42)
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("op %d differs between identical-seed runs: %+v vs %+v", i, a[i], b[i])
		}
	}
}

// Guards against exactly the pathology that made NParticipants necessary:
// if every generated order shared one owner (or too few), self-trade
// prevention (added after this generator existed) would dominate the
// synthetic workload's behavior instead of realistic multi-participant
// matching.
func TestGeneratorAssignsManyDistinctOwners(t *testing.T) {
	p := DefaultParams(10000)
	g := New(p, 3)

	owners := map[uint32]int{}
	for i := 0; i < 20_000; i++ {
		op := g.Next()
		if op.Kind == OpSubmit {
			owners[op.Order.Owner]++
			g.Track(op.Order.ID)
		}
	}
	if len(owners) < p.NParticipants/2 {
		t.Errorf("only %d distinct owners appeared out of a %d-participant pool -- "+
			"too concentrated, self-trade prevention would distort benchmark results",
			len(owners), p.NParticipants)
	}
}
