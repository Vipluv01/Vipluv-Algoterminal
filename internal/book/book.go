package book

import "errors"

// level is one price point in the book: a FIFO queue of orders sharing a price.
// Orders join at the tail and match from the head, which is exactly what
// time priority means.
type level struct {
	head  handle
	tail  handle
	total Qty
	count int32
}

// Config describes the instrument's price band. The band is fixed at
// construction because it sizes the tick-indexed level arrays; a real venue
// derives it from the previous close and its circuit limits.
type Config struct {
	MinPx    Price
	MaxPx    Price
	Tick     Price
	Capacity int // maximum simultaneously live orders

	// PriceCollarBps, if nonzero, rejects any LIMIT order whose price
	// deviates from the last traded price by more than this many basis
	// points (1 bps = 0.01%). Fat-finger protection: a "buy at 10000 when
	// the market trades at 100" typo gets rejected outright instead of
	// resting on the book (or worse, immediately sweeping through dozens of
	// price levels). Zero disables the check. Deliberately scoped to LIMIT
	// orders only -- market-order price protection (bounding how far a
	// market order can walk a thin book) is a related but different
	// mechanism, not implemented here.
	PriceCollarBps int64

	// PositionLimit, if nonzero, rejects an order outright if it could push
	// the submitting owner's NET FILLED position (not open-order exposure)
	// beyond +/-PositionLimit, checked pessimistically against the order's
	// full quantity before any matching happens. Zero disables the check.
	PositionLimit Qty
}

// Book is a single-instrument limit order book.
//
// It is deliberately NOT safe for concurrent use. A matching engine that takes
// a lock per order has already lost: the lock serialises everything anyway, and
// adds contention plus nondeterministic interleaving on top. The engine layer
// owns one Book per goroutine and feeds it from a sequenced input stream, which
// is both faster and replayable.
type Book struct {
	cfg    Config
	nTicks int

	bids []level
	asks []level
	bidM *bitmap
	askM *bitmap

	// Stop orders wait here, indexed by trigger price rather than limit price,
	// so finding what a trade has triggered is the same O(1) bitmap probe as
	// finding the best bid.
	stopBuy  []level
	stopSell []level
	stopBuyM  *bitmap
	stopSellM *bitmap

	a     *arena
	index map[OrderID]handle

	fills   []Fill
	evSeq   uint64
	arrSeq  uint64
	lastPx  Price
	hasLast bool

	trades     uint64
	volume     Qty
	stpCancels uint64

	// positions tracks NET FILLED quantity per owner: positive is net long,
	// negative is net short. Updated only on fills (in consume()), never on
	// resting orders -- an order sitting on the book unfilled has not
	// changed anyone's position yet, only their open exposure, which this
	// deliberately does not track (see Config.PositionLimit's doc comment).
	positions map[uint32]int64
}

var errBadConfig = errors.New("book: invalid config")

func New(cfg Config) (*Book, error) {
	if cfg.Tick <= 0 || cfg.MaxPx <= cfg.MinPx || cfg.Capacity <= 0 {
		return nil, errBadConfig
	}
	if (cfg.MaxPx-cfg.MinPx)%cfg.Tick != 0 {
		return nil, errBadConfig
	}
	n := int((cfg.MaxPx-cfg.MinPx)/cfg.Tick) + 1
	b := &Book{
		cfg:       cfg,
		nTicks:    n,
		bids:      makeLevels(n),
		asks:      makeLevels(n),
		bidM:      newBitmap(n),
		askM:      newBitmap(n),
		stopBuy:   makeLevels(n),
		stopSell:  makeLevels(n),
		stopBuyM:  newBitmap(n),
		stopSellM: newBitmap(n),
		a:         newArena(cfg.Capacity),
		index:     make(map[OrderID]handle, cfg.Capacity),
		fills:     make([]Fill, 0, 64),
		positions: make(map[uint32]int64),
	}
	return b, nil
}

func makeLevels(n int) []level {
	l := make([]level, n)
	for i := range l {
		l[i].head, l[i].tail = nilHandle, nilHandle
	}
	return l
}

func (b *Book) tickOf(p Price) int  { return int((p - b.cfg.MinPx) / b.cfg.Tick) }
func (b *Book) pxOf(t int) Price    { return b.cfg.MinPx + Price(t)*b.cfg.Tick }
func (b *Book) validPx(p Price) bool {
	return p >= b.cfg.MinPx && p <= b.cfg.MaxPx && (p-b.cfg.MinPx)%b.cfg.Tick == 0
}

// withinPriceCollar reports whether px is within Config.PriceCollarBps of
// the last traded price. Cross-multiplied (deviation*10000 vs lastPx*bps)
// rather than divided, so this never has to reason about integer-division
// rounding or a division-by-zero edge case on a zero reference price.
func (b *Book) withinPriceCollar(px Price) bool {
	dev := px - b.lastPx
	if dev < 0 {
		dev = -dev
	}
	return dev*10000 <= b.lastPx*Price(b.cfg.PriceCollarBps)
}

// withinPositionLimit reports whether accepting o could NOT push the
// owner's net position beyond +/-PositionLimit, checked pessimistically
// against the order's full quantity -- i.e. assuming the whole order fills,
// even though it may only partially fill or not fill at all. That
// conservatism is deliberate: checking against the actual eventual fill
// quantity would require matching first and unwinding afterward if the
// limit were breached, which this book cannot do (no rollback) and no real
// venue does either -- pre-trade risk checks are pessimistic by nature.
// applyPositionDelta records the effect of one fill on one participant's
// net position: buying increases it, selling decreases it. Called once per
// side per fill from consume() -- this is the ONLY place positions change,
// which is what makes "position" mean "net FILLED quantity", never
// unfilled resting exposure.
func (b *Book) applyPositionDelta(owner uint32, side Side, qty Qty) {
	if side == Buy {
		b.positions[owner] += int64(qty)
	} else {
		b.positions[owner] -= int64(qty)
	}
}

func (b *Book) withinPositionLimit(o Order) bool {
	delta := int64(o.Qty)
	if o.Side == Sell {
		delta = -delta
	}
	newPos := b.positions[o.Owner] + delta
	if newPos < 0 {
		newPos = -newPos
	}
	return newPos <= int64(b.cfg.PositionLimit)
}

// Submit processes one order to completion and returns the fills it generated.
//
// The returned slice aliases an internal buffer that is reused on the next
// call. Callers that need to retain fills must copy them. This is the usual
// tradeoff for keeping the hot path allocation-free, and it is why the engine
// layer copies into its event log immediately.
func (b *Book) Submit(o Order) ([]Fill, RejectReason) {
	b.fills = b.fills[:0]
	r := b.submit(o)
	if r != RejectNone {
		return nil, r
	}
	return b.fills, RejectNone
}

// submit does the work without resetting the fill buffer, so triggered stop
// orders can append to the same batch as the trade that triggered them.
func (b *Book) submit(o Order) RejectReason {
	if o.Qty <= 0 {
		return RejectZeroQty
	}
	if _, dup := b.index[o.ID]; dup {
		return RejectDuplicateID
	}

	switch o.Type {
	case StopLimitOrder:
		if !b.validPx(o.StopPx) || !b.validPx(o.Px) {
			return RejectPriceOutOfRange
		}
		// A stop whose trigger has already been passed by the market is not a
		// stop at all -- it is a marketable limit order, and parking it would
		// leave it stranded until the price happened to cross back.
		if b.hasLast && b.triggered(o.Side, o.StopPx) {
			o.Type = LimitOrder
			return b.submit(o)
		}
		return b.park(o)

	case MarketOrder:
		// A market order is a limit order priced at the far edge of the band.
		// Modelling it this way means one matching path instead of two.
		if o.Side == Buy {
			o.Px = b.cfg.MaxPx
		} else {
			o.Px = b.cfg.MinPx
		}
		// Market orders never rest; an unfillable remainder is cancelled.
		if o.TIF == GTC {
			o.TIF = IOC
		}

	case LimitOrder:
		if !b.validPx(o.Px) {
			return RejectPriceOutOfRange
		}
	}

	// Fat-finger collar: only meaningful once there is a reference price to
	// deviate from, and deliberately scoped to LIMIT orders -- a MarketOrder
	// has already been re-priced to the band edge by this point (see the
	// MarketOrder case above), so checking it against the collar would
	// reject nearly every market order for the wrong reason.
	if o.Type == LimitOrder && b.cfg.PriceCollarBps > 0 && b.hasLast {
		if !b.withinPriceCollar(o.Px) {
			return RejectPriceCollar
		}
	}

	if b.cfg.PositionLimit > 0 && !b.withinPositionLimit(o) {
		return RejectPositionLimit
	}

	limit := int32(b.tickOf(o.Px))

	if o.TIF == FOK && b.available(o.Side, o.Owner, limit) < o.Qty {
		return RejectFOKUnfillable
	}

	remaining := b.matchAgainst(o.Side, o.ID, o.Owner, o.Qty, limit)

	if remaining > 0 && o.TIF == GTC {
		if r := b.rest(o, remaining); r != RejectNone {
			return r
		}
	}

	b.runTriggers()
	return RejectNone
}

// matchAgainst aggresses into the opposite book until the order is filled or
// the next price is worse than the limit. It returns the unfilled remainder.
func (b *Book) matchAgainst(side Side, id OrderID, owner uint32, remaining Qty, limit int32) Qty {
	if side == Buy {
		for remaining > 0 {
			t := b.askM.min()
			if t < 0 || int32(t) > limit {
				break
			}
			remaining = b.consume(&b.asks[t], remaining, id, owner, Buy, Sell)
		}
		return remaining
	}
	for remaining > 0 {
		t := b.bidM.max()
		if t < 0 || int32(t) < limit {
			break
		}
		remaining = b.consume(&b.bids[t], remaining, id, owner, Sell, Buy)
	}
	return remaining
}

// consume fills against one price level in strict arrival order.
//
// Self-trade prevention: a resting order owned by the SAME participant as
// the incoming order is never matched against. Real venues offer several
// STP modes (cancel newest, cancel oldest, cancel both, decrement-and-
// cancel); this implements cancel-oldest -- the resting order is cancelled
// outright and the incoming order continues walking the level as if it had
// never been there. That is the version that keeps the book's own
// invariants trivially true afterward (no stale, permanently-unmatchable
// order left sitting at the front of a queue silently blocking everyone
// behind it) and matches what most venues default to. The cancelled
// quantity produces no Fill and is not double-counted anywhere: it is
// simply removed from the book, same bookkeeping path as an ordinary
// Cancel.
func (b *Book) consume(lv *level, remaining Qty, takerID OrderID, takerOwner uint32, takerSide, makerSide Side) Qty {
	for remaining > 0 && lv.head != nilHandle {
		h := lv.head
		n := b.a.at(h)

		if n.owner == takerOwner {
			id := n.id
			b.unlink(lv, h, makerSide)
			delete(b.index, id)
			b.a.release(h)
			b.stpCancels++
			continue
		}

		q := remaining
		if n.qty < q {
			q = n.qty
		}

		b.evSeq++
		b.fills = append(b.fills, Fill{
			Seq:       b.evSeq,
			TakerID:   takerID,
			MakerID:   n.id,
			Px:        n.px, // maker's price: the resting side gets the improvement
			Qty:       q,
			TakerSide: takerSide,
		})

		remaining -= q
		n.qty -= q
		lv.total -= q
		b.volume += q
		b.trades++
		b.lastPx, b.hasLast = n.px, true

		// Position tracking: both sides of a fill move, in opposite
		// directions -- the taker's position changes per takerSide, the
		// maker's per makerSide (always the opposite side, by construction:
		// a buy only ever matches against a resting sell and vice versa).
		b.applyPositionDelta(takerOwner, takerSide, q)
		b.applyPositionDelta(n.owner, makerSide, q)

		if n.qty == 0 {
			id := n.id
			b.unlink(lv, h, makerSide)
			delete(b.index, id)
			b.a.release(h)
		}
	}
	return remaining
}

func (b *Book) unlink(lv *level, h handle, side Side) {
	n := b.a.at(h)
	if n.prev != nilHandle {
		b.a.at(n.prev).next = n.next
	} else {
		lv.head = n.next
	}
	if n.next != nilHandle {
		b.a.at(n.next).prev = n.prev
	} else {
		lv.tail = n.prev
	}
	lv.count--
	// unlink is the single owner of level aggregates. consume() has already
	// decremented total by whatever it filled, leaving n.qty at 0, so this is
	// a no-op on the fill path and the true remainder on the cancel path.
	lv.total -= n.qty
	if lv.count == 0 {
		lv.head, lv.tail = nilHandle, nilHandle
		// total is deliberately NOT forced to zero here. Clamping it would hide
		// exactly the arithmetic drift the invariant checker exists to catch.
		switch {
		case n.pending && side == Buy:
			b.stopBuyM.clear(int(n.tick))
		case n.pending:
			b.stopSellM.clear(int(n.tick))
		case side == Buy:
			b.bidM.clear(int(n.tick))
		default:
			b.askM.clear(int(n.tick))
		}
	}
}

// rest links an unfilled remainder onto the book at the tail of its price
// level, which is where time priority is actually enforced.
func (b *Book) rest(o Order, remaining Qty) RejectReason {
	h := b.a.alloc()
	if h == nilHandle {
		return RejectBookFull
	}
	t := b.tickOf(o.Px)
	b.arrSeq++
	n := b.a.at(h)
	*n = node{
		id: o.ID, px: o.Px, qty: remaining, seq: b.arrSeq,
		prev: nilHandle, next: nilHandle, tick: int32(t),
		owner: o.Owner, side: o.Side, typ: o.Type, tif: o.TIF,
	}

	var lv *level
	if o.Side == Buy {
		lv = &b.bids[t]
		b.bidM.set(t)
	} else {
		lv = &b.asks[t]
		b.askM.set(t)
	}
	b.link(lv, h)
	b.index[o.ID] = h
	return RejectNone
}

func (b *Book) link(lv *level, h handle) {
	n := b.a.at(h)
	if lv.tail == nilHandle {
		lv.head, lv.tail = h, h
	} else {
		n.prev = lv.tail
		b.a.at(lv.tail).next = h
		lv.tail = h
	}
	lv.count++
	lv.total += n.qty
}

// Cancel removes a resting or pending order. Cancelling is O(1): the index
// gives the handle, and the intrusive list means unlinking never scans.
func (b *Book) Cancel(id OrderID) RejectReason {
	h, ok := b.index[id]
	if !ok {
		return RejectUnknownOrder
	}
	n := b.a.at(h)
	lv := b.levelFor(n)
	b.unlink(lv, h, n.side)
	delete(b.index, id)
	b.a.release(h)
	return RejectNone
}

func (b *Book) levelFor(n *node) *level {
	switch {
	case n.pending && n.side == Buy:
		return &b.stopBuy[n.tick]
	case n.pending:
		return &b.stopSell[n.tick]
	case n.side == Buy:
		return &b.bids[n.tick]
	default:
		return &b.asks[n.tick]
	}
}
