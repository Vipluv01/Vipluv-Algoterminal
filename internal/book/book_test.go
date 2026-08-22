package book

import "testing"

func newTestBook(t *testing.T) *Book {
	t.Helper()
	b, err := New(Config{MinPx: 1, MaxPx: 20000, Tick: 1, Capacity: 1 << 16})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return b
}

func mustSubmit(t *testing.T, b *Book, o Order) []Fill {
	t.Helper()
	f, r := b.Submit(o)
	if r != RejectNone {
		t.Fatalf("order %d rejected: %s", o.ID, r)
	}
	out := append([]Fill(nil), f...) // Submit's buffer is reused
	if err := b.Check(); err != nil {
		t.Fatalf("invariant violated after order %d: %v", o.ID, err)
	}
	return out
}

// The queue at a price level is the venue's promise to whoever got there
// first. If a later order at the same price fills ahead of an earlier one,
// every market maker's model of the venue is wrong.
func TestPriceTimePriority(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 10, Side: Buy})
	mustSubmit(t, b, Order{ID: 2, Owner: 1002, Px: 100, Qty: 10, Side: Buy})
	mustSubmit(t, b, Order{ID: 3, Owner: 1003, Px: 100, Qty: 10, Side: Buy})

	fills := mustSubmit(t, b, Order{ID: 4, Owner: 1004, Px: 100, Qty: 15, Side: Sell})

	if len(fills) != 2 {
		t.Fatalf("want 2 fills, got %d", len(fills))
	}
	if fills[0].MakerID != 1 || fills[0].Qty != 10 {
		t.Errorf("first fill should exhaust order 1 for 10, got maker=%d qty=%d", fills[0].MakerID, fills[0].Qty)
	}
	if fills[1].MakerID != 2 || fills[1].Qty != 5 {
		t.Errorf("second fill should take 5 from order 2, got maker=%d qty=%d", fills[1].MakerID, fills[1].Qty)
	}
	if rem, _ := b.Remaining(2); rem != 5 {
		t.Errorf("order 2 should have 5 left, has %d", rem)
	}
	if _, live := b.Remaining(3); !live {
		t.Error("order 3 should be untouched")
	}
}

// A crossing order trades at the resting order's price, not its own. The
// aggressor named a worst acceptable price; the queue sets the actual one.
func TestMakerPriceImprovement(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 10, Side: Buy})

	fills := mustSubmit(t, b, Order{ID: 2, Owner: 1002, Px: 95, Qty: 10, Side: Sell})
	if len(fills) != 1 {
		t.Fatalf("want 1 fill, got %d", len(fills))
	}
	if fills[0].Px != 100 {
		t.Errorf("seller asked 95 against a bid of 100, should fill at 100, got %d", fills[0].Px)
	}
}

func TestMarketOrderWalksTheBook(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 101, Qty: 5, Side: Sell})
	mustSubmit(t, b, Order{ID: 2, Owner: 1002, Px: 102, Qty: 5, Side: Sell})
	mustSubmit(t, b, Order{ID: 3, Owner: 1003, Px: 103, Qty: 5, Side: Sell})

	fills := mustSubmit(t, b, Order{ID: 4, Owner: 1004, Qty: 12, Side: Buy, Type: MarketOrder})
	if len(fills) != 3 {
		t.Fatalf("want 3 fills across 3 levels, got %d", len(fills))
	}
	if fills[0].Px != 101 || fills[1].Px != 102 || fills[2].Px != 103 {
		t.Errorf("should sweep upward through levels, got %d/%d/%d", fills[0].Px, fills[1].Px, fills[2].Px)
	}
	// An unfilled market remainder must never rest: it would sit at the band
	// edge as a standing order to trade at any price.
	if _, live := b.Remaining(4); live {
		t.Error("market order remainder rested on the book")
	}
}

func TestIOCCancelsRemainder(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 3, Side: Sell})

	fills := mustSubmit(t, b, Order{ID: 2, Owner: 1002, Px: 100, Qty: 10, Side: Buy, TIF: IOC})
	if len(fills) != 1 || fills[0].Qty != 3 {
		t.Fatalf("want one 3-lot fill, got %+v", fills)
	}
	if _, live := b.Remaining(2); live {
		t.Error("IOC remainder should not rest")
	}
}

func TestFOKAllOrNothing(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 5, Side: Sell})

	// Not enough liquidity: must reject without trading at all.
	if _, r := b.Submit(Order{ID: 2, Owner: 1002, Px: 100, Qty: 10, Side: Buy, TIF: FOK}); r != RejectFOKUnfillable {
		t.Fatalf("want RejectFOKUnfillable, got %s", r)
	}
	if rem, _ := b.Remaining(1); rem != 5 {
		t.Errorf("rejected FOK must not have traded, resting qty now %d", rem)
	}

	// Exactly enough: must fill completely.
	fills := mustSubmit(t, b, Order{ID: 3, Owner: 1003, Px: 100, Qty: 5, Side: Buy, TIF: FOK})
	if len(fills) != 1 || fills[0].Qty != 5 {
		t.Fatalf("want a complete 5-lot fill, got %+v", fills)
	}
}

func TestStopLimitTriggers(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 10, Side: Sell})
	mustSubmit(t, b, Order{ID: 2, Owner: 1002, Px: 100, Qty: 10, Side: Buy}) // trade at 100

	// Sell stop below the market: parked, not live.
	mustSubmit(t, b, Order{ID: 3, Owner: 1003, StopPx: 95, Px: 90, Qty: 10, Side: Sell, Type: StopLimitOrder})
	if _, _, ok := b.BestAsk(); ok {
		t.Fatal("parked stop should not appear in the visible book")
	}

	// Drive the price down through 95 and the stop must activate.
	mustSubmit(t, b, Order{ID: 4, Owner: 1004, Px: 94, Qty: 10, Side: Buy})
	mustSubmit(t, b, Order{ID: 5, Owner: 1005, Px: 94, Qty: 10, Side: Sell})

	if _, live := b.Remaining(3); !live {
		t.Fatal("stop should have triggered and rested as a limit order")
	}
	if err := b.Check(); err != nil {
		t.Fatalf("invariants after trigger: %v", err)
	}
}

// A stop whose trigger the market has already passed is not a stop. Parking it
// would strand the order until the price happened to cross back.
func TestStopAlreadyBreachedGoesLive(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 10, Side: Sell})
	mustSubmit(t, b, Order{ID: 2, Owner: 1002, Px: 100, Qty: 10, Side: Buy}) // last = 100

	mustSubmit(t, b, Order{ID: 3, Owner: 1003, StopPx: 90, Px: 105, Qty: 5, Side: Buy, Type: StopLimitOrder})
	bid, _, ok := b.BestBid()
	if !ok || bid != 105 {
		t.Fatalf("breached buy stop should be live at 105, best bid = %d (ok=%v)", bid, ok)
	}
}

func TestRejects(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 10, Side: Buy})

	for _, tc := range []struct {
		name string
		o    Order
		want RejectReason
	}{
		{"duplicate id", Order{ID: 1, Owner: 1001, Px: 101, Qty: 5, Side: Buy}, RejectDuplicateID},
		{"zero qty", Order{ID: 2, Owner: 1002, Px: 101, Qty: 0, Side: Buy}, RejectZeroQty},
		{"negative qty", Order{ID: 3, Owner: 1003, Px: 101, Qty: -5, Side: Buy}, RejectZeroQty},
		{"above band", Order{ID: 4, Owner: 1004, Px: 999999, Qty: 5, Side: Buy}, RejectPriceOutOfRange},
		{"below band", Order{ID: 5, Owner: 1005, Px: 0, Qty: 5, Side: Buy}, RejectPriceOutOfRange},
	} {
		if _, r := b.Submit(tc.o); r != tc.want {
			t.Errorf("%s: want %s, got %s", tc.name, tc.want, r)
		}
	}
	if err := b.Check(); err != nil {
		t.Fatalf("rejects must leave the book untouched: %v", err)
	}
}

func TestCancel(t *testing.T) {
	b := newTestBook(t)
	mustSubmit(t, b, Order{ID: 1, Owner: 1001, Px: 100, Qty: 10, Side: Buy})
	mustSubmit(t, b, Order{ID: 2, Owner: 1002, Px: 100, Qty: 10, Side: Buy})

	if r := b.Cancel(1); r != RejectNone {
		t.Fatalf("cancel: %s", r)
	}
	if err := b.Check(); err != nil {
		t.Fatalf("after cancel: %v", err)
	}
	if r := b.Cancel(1); r != RejectUnknownOrder {
		t.Errorf("double cancel should reject, got %s", r)
	}

	// Cancelling the head must promote the next order, not orphan the level.
	fills := mustSubmit(t, b, Order{ID: 3, Owner: 1003, Px: 100, Qty: 10, Side: Sell})
	if len(fills) != 1 || fills[0].MakerID != 2 {
		t.Errorf("order 2 should now be at the front, got %+v", fills)
	}
}
