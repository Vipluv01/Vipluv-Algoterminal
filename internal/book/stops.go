package book

// Stop orders are the part of a matching engine that most toy implementations
// either skip or get subtly wrong, usually by scanning every pending stop after
// every trade. Here they are stored in their own tick-indexed books keyed by
// TRIGGER price, so "what did this trade just trigger?" is the same O(1) bitmap
// probe used to find the best bid -- and the common case, where a trade
// triggers nothing, costs one comparison.

// triggered reports whether the last traded price has reached a stop level.
// A buy stop fires on the way up, a sell stop on the way down.
func (b *Book) triggered(side Side, stopPx Price) bool {
	if side == Buy {
		return b.lastPx >= stopPx
	}
	return b.lastPx <= stopPx
}

// park stores a stop order until its trigger price trades.
func (b *Book) park(o Order) RejectReason {
	h := b.a.alloc()
	if h == nilHandle {
		return RejectBookFull
	}
	t := b.tickOf(o.StopPx)
	b.arrSeq++
	n := b.a.at(h)
	*n = node{
		id: o.ID, px: o.Px, qty: o.Qty, seq: b.arrSeq,
		prev: nilHandle, next: nilHandle, tick: int32(t),
		owner: o.Owner, side: o.Side, typ: o.Type, tif: o.TIF,
		pending: true,
	}

	var lv *level
	if o.Side == Buy {
		lv = &b.stopBuy[t]
		b.stopBuyM.set(t)
	} else {
		lv = &b.stopSell[t]
		b.stopSellM.set(t)
	}
	b.link(lv, h)
	b.index[o.ID] = h
	return RejectNone
}

// maxTriggerCascades bounds stop-triggers-stop chains. A cascade is legitimate
// market behaviour -- it is how flash crashes propagate -- but an unbounded
// loop inside the matching path is not, so we cap it and stop. Reaching the cap
// means the configured price band is being swept end to end.
const maxTriggerCascades = 64

// runTriggers releases every stop the last trade has activated, converts them
// to limit orders, and submits them. Each activated order can trade, which can
// move the last price, which can activate more stops; the loop runs until the
// market goes quiet.
func (b *Book) runTriggers() {
	if !b.hasLast {
		return
	}
	for cascade := 0; cascade < maxTriggerCascades; cascade++ {
		h := b.popTriggered()
		if h == nilHandle {
			return
		}
		n := b.a.at(h)
		o := Order{
			ID: n.id, Owner: n.owner, Px: n.px, Qty: n.qty,
			Side: n.side, Type: LimitOrder, TIF: n.tif,
		}
		id := n.id
		b.unlink(b.levelFor(n), h, n.side)
		delete(b.index, id)
		b.a.release(h)

		// Submitted as an ordinary limit order: a triggered stop-limit has no
		// special privileges, and in particular it does not jump the queue at
		// its limit price.
		b.submit(o)
	}
}

// popTriggered returns one activated stop, or nilHandle. Buy stops trigger
// from the lowest trigger price upward, sell stops from the highest downward,
// which is the order the market would actually reach them in.
func (b *Book) popTriggered() handle {
	last := b.tickOf(b.lastPx)

	if t := b.stopBuyM.min(); t >= 0 && t <= last {
		return b.stopBuy[t].head
	}
	if t := b.stopSellM.max(); t >= 0 && t >= last {
		return b.stopSell[t].head
	}
	return nilHandle
}

// available sums the quantity a taker could reach at or inside the limit,
// EXCLUDING any resting quantity owned by the same participant.
//
// This exclusion is not optional. FOK's entire contract is "fill
// completely, atomically, or reject outright" -- if available() counted
// self-owned resting liquidity, an FOK order could pass this check by
// counting quantity that self-trade prevention will then cancel rather than
// match, leaving the order accepted (RejectNone) but with zero fills and
// nothing resting: neither filled per its own contract nor rejected, just
// silently gone. That was a real bug here, caught by
// TestSelfTradePreventionRespectsFOK, before this function knew about
// ownership at all.
//
// Walking individual orders (not just level totals) is more expensive than
// the level-total-only version this replaced, but FOK is the only caller,
// never the hot path, so the cost is fine.
func (b *Book) available(side Side, owner uint32, limit int32) Qty {
	var total Qty
	if side == Buy {
		for t := b.askM.min(); t >= 0 && int32(t) <= limit; t = b.askM.nextAbove(t) {
			total += b.availableInLevel(&b.asks[t], owner)
		}
		return total
	}
	for t := b.bidM.max(); t >= 0 && int32(t) >= limit; t = b.bidM.nextBelow(t) {
		total += b.availableInLevel(&b.bids[t], owner)
	}
	return total
}

func (b *Book) availableInLevel(lv *level, owner uint32) Qty {
	var total Qty
	for h := lv.head; h != nilHandle; h = b.a.at(h).next {
		n := b.a.at(h)
		if n.owner != owner {
			total += n.qty
		}
	}
	return total
}
