package book

import "fmt"

// Check verifies every structural invariant the book is supposed to maintain.
//
// This exists because a matching engine fails silently. A corrupted book does
// not panic -- it quietly matches the wrong order, or loses one, and the damage
// surfaces later as a reconciliation break nobody can explain. Property tests
// drive random order flow through the book and call Check after every single
// operation, so a violation is caught on the operation that caused it rather
// than a million operations downstream.
//
// Check is O(live orders) and is never called on the hot path.
func (b *Book) Check() error {
	if err := b.checkSide(b.bids, b.bidM, Buy, false); err != nil {
		return fmt.Errorf("bids: %w", err)
	}
	if err := b.checkSide(b.asks, b.askM, Sell, false); err != nil {
		return fmt.Errorf("asks: %w", err)
	}
	if err := b.checkSide(b.stopBuy, b.stopBuyM, Buy, true); err != nil {
		return fmt.Errorf("stop-buy: %w", err)
	}
	if err := b.checkSide(b.stopSell, b.stopSellM, Sell, true); err != nil {
		return fmt.Errorf("stop-sell: %w", err)
	}

	// The defining invariant: a book may touch but never cross. If the best bid
	// is at or above the best ask, a match was missed and the book is showing a
	// trade that should already have happened.
	if bid, _, okB := b.BestBid(); okB {
		if ask, _, okA := b.BestAsk(); okA && bid >= ask {
			return fmt.Errorf("crossed book: bid %d >= ask %d", bid, ask)
		}
	}

	// Every live arena slot must be reachable through the index, and vice
	// versa. A mismatch means an order leaked (untradeable but occupying
	// capacity) or was double-freed (about to be handed out twice).
	if len(b.index) != b.a.live() {
		return fmt.Errorf("index/arena divergence: index=%d arena=%d", len(b.index), b.a.live())
	}
	for id, h := range b.index {
		if int(h) >= len(b.a.nodes) {
			return fmt.Errorf("order %d: handle %d out of range", id, h)
		}
		if n := b.a.at(h); n.id != id {
			return fmt.Errorf("order %d: index points at order %d", id, n.id)
		}
	}

	// Every fill is a transfer between exactly two parties in opposite
	// directions -- one side's +q is the other's -q, by construction in
	// applyPositionDelta. That means the sum of EVERY owner's net position,
	// across the whole market, must always be exactly zero: quantity is
	// neither created nor destroyed by matching, only moved. This is a
	// cheap, powerful check -- a single bug anywhere in position tracking
	// (wrong side, double-counted fill, a maker/taker mixup) shows up here
	// immediately as a nonzero sum, without needing to know which specific
	// owner or fill was wrong.
	var positionSum int64
	for _, p := range b.positions {
		positionSum += p
	}
	if positionSum != 0 {
		return fmt.Errorf("positions do not net to zero: sum=%d (a fill moved quantity "+
			"without an equal and opposite transfer)", positionSum)
	}

	return nil
}

func (b *Book) checkSide(levels []level, bm *bitmap, side Side, pending bool) error {
	for t := range levels {
		lv := &levels[t]
		occupied := lv.count > 0

		// The bitmap is a cache of "is this level non-empty". If it disagrees
		// with the level, best-price lookup returns a price with no liquidity
		// behind it, and the matching loop spins on an empty level.
		if bm.test(t) != occupied {
			return fmt.Errorf("tick %d: bitmap=%v but count=%d", t, bm.test(t), lv.count)
		}
		if !occupied {
			if lv.head != nilHandle || lv.tail != nilHandle || lv.total != 0 {
				return fmt.Errorf("tick %d: empty level not reset", t)
			}
			continue
		}

		var (
			sum  Qty
			n    int32
			prev = nilHandle
			seen = b.a.at(lv.head).seq
		)
		for h := lv.head; h != nilHandle; {
			nd := b.a.at(h)
			if nd.prev != prev {
				return fmt.Errorf("tick %d: broken back-link at order %d", t, nd.id)
			}
			if nd.qty <= 0 {
				return fmt.Errorf("tick %d: order %d linked with qty %d", t, nd.id, nd.qty)
			}
			if int(nd.tick) != t {
				return fmt.Errorf("order %d: filed at tick %d, stored tick %d", nd.id, t, nd.tick)
			}
			if nd.side != side {
				return fmt.Errorf("order %d: %s order on %s side", nd.id, nd.side, side)
			}
			if nd.pending != pending {
				return fmt.Errorf("order %d: pending=%v in pending=%v book", nd.id, nd.pending, pending)
			}
			// Time priority is the promise the book makes to resting orders.
			// Arrival sequence must increase monotonically from head to tail,
			// or someone has jumped the queue.
			if h != lv.head && nd.seq <= seen {
				return fmt.Errorf("tick %d: order %d breaks FIFO (seq %d after %d)", t, nd.id, nd.seq, seen)
			}
			seen = nd.seq

			sum += nd.qty
			n++
			prev = h
			h = nd.next
		}
		if prev != lv.tail {
			return fmt.Errorf("tick %d: tail pointer does not match last node", t)
		}
		// Quantity conservation: the cached aggregate must equal the sum of its
		// parts, or the depth feed is lying to every participant reading it.
		if sum != lv.total {
			return fmt.Errorf("tick %d: total=%d but orders sum to %d", t, lv.total, sum)
		}
		if n != lv.count {
			return fmt.Errorf("tick %d: count=%d but %d orders linked", t, lv.count, n)
		}
	}
	return nil
}
