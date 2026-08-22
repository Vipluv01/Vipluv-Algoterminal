package book

import "testing"

// Self-trade prevention: an order must never match against a resting order
// from the same owner. These test the mechanism directly, distinct from
// book_test.go's tests (which now assign distinct owners specifically so
// they test ORDINARY cross-participant matching without STP interfering).

func TestSelfTradePreventionCancelsTheRestingOrder(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 5, Px: 100, Qty: 10, Side: Buy})

	// Same owner (5) submits a crossing sell -- must NOT fill against its
	// own resting buy. Cancel-oldest: the resting order is removed, no fill
	// is produced, and the incoming order (having nothing left to trade
	// against at this level) rests instead.
	fills := mustSubmit(t, b, Order{ID: 2, Owner: 5, Px: 100, Qty: 10, Side: Sell})

	if len(fills) != 0 {
		t.Fatalf("self-trade must produce zero fills, got %d", len(fills))
	}
	if _, live := b.Remaining(1); live {
		t.Error("the resting order should have been cancelled by STP, not left resting")
	}
	if rem, live := b.Remaining(2); !live || rem != 10 {
		t.Errorf("the incoming order should rest for its full 10 (nothing to trade against), got rem=%d live=%v", rem, live)
	}
	if st := b.Stats(); st.STPCancels != 1 {
		t.Errorf("want 1 STP cancellation recorded, got %d", st.STPCancels)
	}
}

func TestSelfTradePreventionSkipsOwnOrderButFillsAgainstOthers(t *testing.T) {
	b := newTestBook(t)
	// Two resting buys at the SAME price: order 1 (owner 5, arrived first),
	// order 2 (owner 6, arrived second). An incoming sell from owner 5
	// must skip its own order 1 (cancelling it) and fill against order 2
	// instead -- STP does not block the whole level, only the self-owned
	// order within it.
	mustSubmit(t, b, Order{ID: 1, Owner: 5, Px: 100, Qty: 10, Side: Buy})
	mustSubmit(t, b, Order{ID: 2, Owner: 6, Px: 100, Qty: 10, Side: Buy})

	fills := mustSubmit(t, b, Order{ID: 3, Owner: 5, Px: 100, Qty: 10, Side: Sell})

	if len(fills) != 1 {
		t.Fatalf("want exactly 1 fill (against order 2, owner 6), got %d: %+v", len(fills), fills)
	}
	if fills[0].MakerID != 2 {
		t.Errorf("fill should be against order 2 (different owner), got maker=%d", fills[0].MakerID)
	}
	if _, live := b.Remaining(1); live {
		t.Error("order 1 (self-owned) should have been STP-cancelled")
	}
	if _, live := b.Remaining(2); live {
		t.Error("order 2 should be fully filled (not resting)")
	}
}

func TestDifferentOwnersMatchNormally(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 5, Px: 100, Qty: 10, Side: Buy})
	fills := mustSubmit(t, b, Order{ID: 2, Owner: 6, Px: 100, Qty: 10, Side: Sell})

	if len(fills) != 1 || fills[0].Qty != 10 {
		t.Fatalf("different owners should match normally, got %+v", fills)
	}
	if st := b.Stats(); st.STPCancels != 0 {
		t.Errorf("no self-trade occurred, want 0 STP cancellations, got %d", st.STPCancels)
	}
}

func TestSelfTradePreventionRespectsFOK(t *testing.T) {
	// An FOK order whose only available liquidity is its own resting order
	// must reject as unfillable, not silently succeed with zero fills.
	//
	// This was a real bug: available() originally summed level totals
	// without knowing about ownership, so it counted the self-owned resting
	// quantity as "available," let the FOK order past its pre-check, and
	// then self-trade prevention cancelled that liquidity instead of
	// matching it -- leaving the order accepted (RejectNone) with zero
	// fills and nothing resting: neither filled per FOK's own contract nor
	// rejected, just silently gone. Fixed by making available() walk
	// individual orders and exclude the taker's own.
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 5, Px: 100, Qty: 10, Side: Buy})

	fills, r := b.Submit(Order{ID: 2, Owner: 5, Px: 100, Qty: 10, Side: Sell, TIF: FOK})
	if r != RejectFOKUnfillable {
		t.Fatalf("FOK against only self-owned liquidity must reject as unfillable, got %s (fills=%v)", r, fills)
	}
	if _, live := b.Remaining(1); !live {
		t.Error("rejected FOK must not have disturbed the resting order")
	}
}

func TestAvailableExcludesOnlySelfOwnedQuantityNotOthers(t *testing.T) {
	// A mixed level -- some self-owned, some not -- must report only the
	// non-self-owned quantity as available, not zero and not the full total.
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 5, Px: 100, Qty: 10, Side: Buy})  // self
	mustSubmit(t, b, Order{ID: 2, Owner: 6, Px: 100, Qty: 7, Side: Buy})   // other

	// FOK for exactly the non-self-owned quantity should succeed.
	fills, r := b.Submit(Order{ID: 3, Owner: 5, Px: 100, Qty: 7, Side: Sell, TIF: FOK})
	if r != RejectNone {
		t.Fatalf("FOK for exactly the available (non-self) quantity should succeed, got %s", r)
	}
	if len(fills) != 1 || fills[0].MakerID != 2 || fills[0].Qty != 7 {
		t.Errorf("should fill 7 against order 2 only, got %+v", fills)
	}
}

func TestSelfTradePreventionThroughMultipleLevels(t *testing.T) {
	// A market order from owner 5 sweeping three price levels, where the
	// middle level belongs to owner 5 itself, must skip only that level's
	// order and still fill against the other two.
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1, Px: 101, Qty: 5, Side: Sell})
	mustSubmit(t, b, Order{ID: 2, Owner: 5, Px: 102, Qty: 5, Side: Sell}) // self-owned
	mustSubmit(t, b, Order{ID: 3, Owner: 3, Px: 103, Qty: 5, Side: Sell})

	fills := mustSubmit(t, b, Order{ID: 4, Owner: 5, Qty: 15, Side: Buy, Type: MarketOrder})

	if len(fills) != 2 {
		t.Fatalf("want 2 fills (order 2 skipped via STP), got %d: %+v", len(fills), fills)
	}
	if fills[0].Px != 101 || fills[1].Px != 103 {
		t.Errorf("should fill at 101 and 103, skipping the self-owned 102 level, got %d and %d",
			fills[0].Px, fills[1].Px)
	}
	if _, live := b.Remaining(2); live {
		t.Error("the self-owned order at 102 should have been STP-cancelled, not left resting")
	}
	// 15 requested, 5+5=10 filled from the two non-self levels, 5 unfilled
	// (order 2's 5 was cancelled, not available to fill against) -- the
	// remainder rests since this was a GTC-equivalent... actually market
	// orders are IOC, so it should NOT rest.
	if _, live := b.Remaining(4); live {
		t.Error("market order remainder must not rest (TIF is forced to IOC)")
	}
}
