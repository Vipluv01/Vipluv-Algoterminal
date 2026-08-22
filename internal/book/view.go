package book

// PriceLevel is one rung of a depth snapshot.
type PriceLevel struct {
	Px    Price
	Qty   Qty
	Count int32
}

// Stats is a cheap summary of book state, safe to poll every tick.
type Stats struct {
	Trades     uint64
	Volume     Qty
	LiveOrders int
	Sequence   uint64
	STPCancels uint64 // orders removed by self-trade prevention, never filled
}

func (b *Book) BestBid() (Price, Qty, bool) {
	t := b.bidM.max()
	if t < 0 {
		return 0, 0, false
	}
	return b.pxOf(t), b.bids[t].total, true
}

func (b *Book) BestAsk() (Price, Qty, bool) {
	t := b.askM.min()
	if t < 0 {
		return 0, 0, false
	}
	return b.pxOf(t), b.asks[t].total, true
}

// Mid returns the midpoint, valid only when both sides are populated. A book
// with one empty side has no meaningful mid, and returning a fabricated one is
// how bad marks propagate into P&L.
func (b *Book) Mid() (Price, bool) {
	bid, _, okB := b.BestBid()
	ask, _, okA := b.BestAsk()
	if !okB || !okA {
		return 0, false
	}
	return (bid + ask) / 2, true
}

// Spread returns the bid-ask spread in ticks.
func (b *Book) Spread() (Price, bool) {
	bid, _, okB := b.BestBid()
	ask, _, okA := b.BestAsk()
	if !okB || !okA {
		return 0, false
	}
	return (ask - bid) / b.cfg.Tick, true
}

func (b *Book) LastPx() (Price, bool) { return b.lastPx, b.hasLast }

func (b *Book) Stats() Stats {
	return Stats{
		Trades: b.trades, Volume: b.volume, LiveOrders: b.a.live(),
		Sequence: b.evSeq, STPCancels: b.stpCancels,
	}
}

// Depth returns up to n levels per side, best first. It allocates, so it is a
// query API for the gateway and the simulator, never something the matching
// path calls.
func (b *Book) Depth(n int) (bids, asks []PriceLevel) {
	bids = make([]PriceLevel, 0, n)
	for t := b.bidM.max(); t >= 0 && len(bids) < n; t = b.bidM.nextBelow(t) {
		bids = append(bids, PriceLevel{b.pxOf(t), b.bids[t].total, b.bids[t].count})
	}
	asks = make([]PriceLevel, 0, n)
	for t := b.askM.min(); t >= 0 && len(asks) < n; t = b.askM.nextAbove(t) {
		asks = append(asks, PriceLevel{b.pxOf(t), b.asks[t].total, b.asks[t].count})
	}
	return bids, asks
}

// Tick exposes the configured tick size for callers that must round prices.
func (b *Book) Tick() Price { return b.cfg.Tick }

// Band exposes the configured price limits.
func (b *Book) Band() (Price, Price) { return b.cfg.MinPx, b.cfg.MaxPx }

// Remaining returns the unfilled quantity of a live order.
func (b *Book) Remaining(id OrderID) (Qty, bool) {
	h, ok := b.index[id]
	if !ok {
		return 0, false
	}
	return b.a.at(h).qty, true
}

// Position returns owner's net filled position: positive is net long,
// negative is net short, zero if the owner has never traded. Reflects only
// FILLED quantity, never open resting-order exposure (see
// Config.PositionLimit's doc comment for why that distinction is
// deliberate).
func (b *Book) Position(owner uint32) int64 {
	return b.positions[owner]
}
