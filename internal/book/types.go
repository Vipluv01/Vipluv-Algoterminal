// Package book implements a limit order book with strict price-time (FIFO)
// priority.
//
// Three decisions drive the whole design and are worth stating up front:
//
//  1. Prices are integer ticks, never floats. Floating point in a matching
//     engine is a correctness bug: 0.1+0.2 != 0.3 means two orders that should
//     cross may not, and fills stop being reproducible.
//  2. Orders live in a flat arena and are addressed by uint32 handles rather
//     than pointers. Go's GC scans pointers; a book holding millions of
//     pointer-linked orders pays for that in tail latency. Handles are opaque
//     integers, so the arena is invisible to the collector.
//  3. Nothing here reads the clock or iterates a map. Both are sources of
//     nondeterminism, and determinism is what makes replay possible.
package book

// Price is a whole number of ticks, not a currency amount. Converting to and
// from human prices happens at the API edge, never in here.
type Price int64

// Qty is an integer quantity in the instrument's minimum lot.
type Qty int64

// OrderID is assigned by the caller and must be unique for the book's lifetime.
type OrderID uint64

type Side uint8

const (
	Buy Side = iota
	Sell
)

func (s Side) String() string {
	if s == Buy {
		return "BUY"
	}
	return "SELL"
}

// Opposite returns the side that this side trades against.
func (s Side) Opposite() Side {
	return s ^ 1
}

// TimeInForce controls what happens to the portion of an order that cannot be
// filled immediately.
type TimeInForce uint8

const (
	// GTC rests any unfilled remainder on the book.
	GTC TimeInForce = iota
	// IOC fills what it can immediately and cancels the remainder.
	IOC
	// FOK fills the order in its entirety or not at all.
	FOK
)

type OrdType uint8

const (
	LimitOrder OrdType = iota
	MarketOrder
	StopLimitOrder
)

// Order is the caller-facing description of an order. It is copied into the
// arena on accept, so the caller may reuse it freely afterwards.
type Order struct {
	ID      OrderID
	Owner   uint32
	Px      Price // ignored for MarketOrder
	StopPx  Price // trigger price, StopLimitOrder only
	Qty     Qty
	Side    Side
	Type    OrdType
	TIF     TimeInForce
}

// Fill records one match. Every fill has exactly one aggressor (taker) and one
// resting order (maker); the price is always the maker's, which is what gives
// the resting side price improvement.
type Fill struct {
	Seq     uint64
	TakerID OrderID
	MakerID OrderID
	Px      Price
	Qty     Qty
	// TakerSide is the side of the aggressing order. Trade sign analysis
	// downstream (order flow imbalance, Kyle's lambda) needs this.
	TakerSide Side
}

// RejectReason explains why an order never reached the book.
type RejectReason uint8

const (
	RejectNone RejectReason = iota
	RejectPriceOutOfRange
	RejectDuplicateID
	RejectZeroQty
	RejectFOKUnfillable
	RejectUnknownOrder
	RejectBookFull
	RejectPriceCollar
	RejectPositionLimit
)

var rejectText = [...]string{
	"none",
	"price outside configured band",
	"duplicate order id",
	"quantity must be positive",
	"fill-or-kill could not be fully filled",
	"unknown order id",
	"order arena exhausted",
	"price outside fat-finger collar of last trade",
	"would exceed owner's position limit",
}

func (r RejectReason) String() string { return rejectText[r] }
