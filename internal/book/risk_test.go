package book

import "testing"

// Pre-trade risk checks: price collar (fat-finger protection) and position
// limits. Both are opt-in via Config -- zero means disabled, and every
// existing test in this package runs with both disabled, which is itself
// meaningful: it proves position tracking (needed for the limit check) and
// the new zero-sum invariant hold silently in the background even when no
// caller ever asks for the risk checks themselves.

func newRiskTestBook(t *testing.T, collarBps int64, posLimit Qty) *Book {
	t.Helper()
	b, err := New(Config{
		MinPx: 1, MaxPx: 20000, Tick: 1, Capacity: 1 << 12,
		PriceCollarBps: collarBps, PositionLimit: posLimit,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return b
}

func TestPriceCollarHasNoEffectBeforeAnyTrade(t *testing.T) {
	// No reference price exists yet -- an absurd price must still be
	// accepted, since there's nothing to compare it against. The collar
	// protects against a fat-finger relative to a KNOWN market, not an
	// opinion about what a "reasonable" first price is.
	b := newRiskTestBook(t, 100, 0) // 1% collar
	if _, r := b.Submit(Order{ID: 1, Owner: 1, Px: 19000, Qty: 10, Side: Buy}); r != RejectNone {
		t.Fatalf("first order ever should not be collared (no reference price yet), got %s", r)
	}
}

func TestPriceCollarRejectsFarFromLastTrade(t *testing.T) {
	b := newRiskTestBook(t, 100, 0) // 1% = 100 bps
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: Sell})
	mustSubmit(t, b, Order{ID: 2, Owner: 2, Px: 100, Qty: 10, Side: Buy}) // trade at 100 -> lastPx=100

	// 1% of 100 is 1 tick; 110 is 10% away -- must reject.
	if _, r := b.Submit(Order{ID: 3, Owner: 3, Px: 110, Qty: 5, Side: Buy}); r != RejectPriceCollar {
		t.Fatalf("want RejectPriceCollar for a price 10%% from last trade under a 1%% collar, got %s", r)
	}
	if err := b.Check(); err != nil {
		t.Fatalf("a rejected order must leave the book untouched: %v", err)
	}
}

func TestPriceCollarAllowsWithinRange(t *testing.T) {
	b := newRiskTestBook(t, 500, 0) // 5% collar
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: Sell})
	mustSubmit(t, b, Order{ID: 2, Owner: 2, Px: 100, Qty: 10, Side: Buy})

	// 104 is 4% away -- within a 5% collar, must be accepted.
	if _, r := b.Submit(Order{ID: 3, Owner: 3, Px: 104, Qty: 5, Side: Buy}); r != RejectNone {
		t.Fatalf("price within the collar should be accepted, got %s", r)
	}
}

func TestPriceCollarDoesNotApplyToMarketOrders(t *testing.T) {
	// A market order has no chosen price to collar -- it's re-priced to
	// the band edge internally (e.g. 20000, far from any real lastPx) as a
	// matching-path simplification, and collaring THAT re-priced value
	// would reject nearly every market order for the wrong reason.
	//
	// Every LIMIT order here is deliberately priced at EXACTLY lastPx (zero
	// deviation), so it passes the collar regardless of how tight it is --
	// isolating the one thing this test actually checks: does the market
	// order's own internal band-edge re-pricing get exempted correctly.
	b := newRiskTestBook(t, 1, 0) // an absurdly tight 0.01% collar
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: Sell})
	mustSubmit(t, b, Order{ID: 2, Owner: 2, Px: 100, Qty: 10, Side: Buy}) // lastPx=100
	mustSubmit(t, b, Order{ID: 3, Owner: 1, Px: 100, Qty: 5, Side: Sell}) // rests exactly at lastPx

	if _, r := b.Submit(Order{ID: 4, Owner: 4, Qty: 5, Side: Buy, Type: MarketOrder}); r != RejectNone {
		t.Fatalf("market orders must be exempt from the price collar, got %s", r)
	}
}

func TestPositionLimitRejectsBreach(t *testing.T) {
	b := newRiskTestBook(t, 0, 20)
	// Owner 1's own setup order must stay within ITS OWN limit too -- the
	// check is pessimistic against an order's full size regardless of
	// whether it rests or fills, so a 30-qty order here would itself be
	// rejected under a 20 limit before the test even gets to what it means
	// to check.
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 20, Side: Sell})

	// Owner 2 buying 25 would put them at position +25, over the limit of 20.
	if _, r := b.Submit(Order{ID: 2, Owner: 2, Px: 100, Qty: 25, Side: Buy}); r != RejectPositionLimit {
		t.Fatalf("want RejectPositionLimit for an order that would breach the limit, got %s", r)
	}
	if pos := b.Position(2); pos != 0 {
		t.Errorf("rejected order must not have moved the position, got %d", pos)
	}
}

func TestPositionLimitAllowsWithinBound(t *testing.T) {
	b := newRiskTestBook(t, 0, 20)
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 20, Side: Sell})

	fills := mustSubmit(t, b, Order{ID: 2, Owner: 2, Px: 100, Qty: 20, Side: Buy})
	if len(fills) != 1 || fills[0].Qty != 20 {
		t.Fatalf("order exactly at the limit should fill, got %+v", fills)
	}
	if pos := b.Position(2); pos != 20 {
		t.Errorf("want position 20, got %d", pos)
	}
	if pos := b.Position(1); pos != -20 {
		t.Errorf("want maker position -20 (sold), got %d", pos)
	}
}

func TestPositionLimitAppliesToShortSideToo(t *testing.T) {
	b := newRiskTestBook(t, 0, 20)
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 20, Side: Buy})

	if _, r := b.Submit(Order{ID: 2, Owner: 2, Px: 100, Qty: 25, Side: Sell}); r != RejectPositionLimit {
		t.Fatalf("a short position breaching the limit must reject too, got %s", r)
	}
}

func TestPositionTrackingReflectsOnlyFillsNotRestingOrders(t *testing.T) {
	// A resting (unfilled) order must NOT itself count as a position change
	// -- only actual fills update Position(). This is orthogonal to the
	// PRE-TRADE check (which IS pessimistic about an order's own full
	// size); this test is about what Position() reports, not about
	// whether an order is admitted.
	b := newTestBook(t) // no limit configured -- isolating position tracking itself
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 1000, Side: Buy}) // rests, nothing to match
	if pos := b.Position(1); pos != 0 {
		t.Errorf("an unfilled resting order must not move position, got %d", pos)
	}
}

func TestPositionLimitDoesNotAccumulateAcrossSeparateRestingOrders(t *testing.T) {
	// A known, documented scoping consequence of checking only REALIZED
	// position (not open exposure): each order is checked pessimistically
	// against the owner's currently-FILLED position, not against other
	// still-resting orders from the same owner. Three separate 15-qty
	// resting sells from the same owner, under a 20 limit, are each
	// individually admitted (0 filled so far + 15 <= 20), even though
	// their combined resting size (45) exceeds the limit. This is exactly
	// what Config.PositionLimit's doc comment means by "not open-order
	// exposure" -- demonstrated concretely, not just asserted in a comment.
	b := newRiskTestBook(t, 0, 20)
	for i, id := range []OrderID{1, 2, 3} {
		if _, r := b.Submit(Order{ID: id, Owner: 9, Px: Price(100 + i), Qty: 15, Side: Sell}); r != RejectNone {
			t.Fatalf("resting order %d should be admitted (checked against 0 filled position), got %s", id, r)
		}
	}
	if pos := b.Position(9); pos != 0 {
		t.Errorf("none of these have filled yet, position should still be 0, got %d", pos)
	}
}

func TestZeroSumInvariantHoldsAcrossMultipleFills(t *testing.T) {
	b := newTestBook(t) // no risk limits -- this test is about the invariant, not the checks
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: Buy})
	mustSubmit(t, b, Order{ID: 2, Owner: 2, Px: 100, Qty: 10, Side: Buy})
	mustSubmit(t, b, Order{ID: 3, Owner: 3, Px: 100, Qty: 15, Side: Sell}) // fills against both

	if err := b.Check(); err != nil {
		t.Fatalf("zero-sum invariant should hold: %v", err)
	}
	sum := b.Position(1) + b.Position(2) + b.Position(3)
	if sum != 0 {
		t.Errorf("positions should net to zero, got sum=%d (1=%d 2=%d 3=%d)",
			sum, b.Position(1), b.Position(2), b.Position(3))
	}
}
