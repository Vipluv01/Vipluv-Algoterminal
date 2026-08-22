// Package workload generates realistic order flow for benchmarking.
//
// Benchmarking a matching engine with uniformly random prices and sizes is
// indefensible -- the first question any reviewer asks is "realistic under
// what workload?", and "uniform random" is not an answer a real market ever
// produces. Real order flow has three properties this generator reproduces:
// most orders cluster tightly around the current best price rather than
// spreading evenly across the whole band; sizes follow a heavy-tailed
// distribution (many small orders, occasionally a large one); and a large
// fraction of orders are cancelled rather than filled, because that is what
// resting limit orders mostly do in a real market.
package workload

import (
	"math"
	"math/rand"

	"github.com/vipluv/bourse/internal/book"
)

type OpKind uint8

const (
	OpSubmit OpKind = iota
	OpCancel
)

type Op struct {
	Kind     OpKind
	Order    book.Order
	CancelID book.OrderID
}

// Params controls the shape of the generated flow. Defaults are chosen to
// resemble a liquid single-name equity book, not any particular real
// instrument.
type Params struct {
	// MidPrice is the centre the generator trades around; it does NOT move
	// as a side effect of generation, since this package produces the INPUT
	// to a benchmark, not a price simulation (see internal/sim for that).
	MidPrice book.Price
	Tick     book.Price

	// PriceSpreadTicks controls clustering: order prices are drawn from a
	// distribution concentrated within this many ticks of MidPrice, with
	// occasional orders further out.
	PriceSpreadTicks int

	// CancelProbability is the chance any given step cancels a resting order
	// instead of submitting a new one, once there are live orders to cancel.
	CancelProbability float64

	// MarketOrderProbability and StopProbability partition the remaining
	// submits between limit (the default), market, and stop-limit orders.
	MarketOrderProbability float64
	StopOrderProbability   float64

	// SweepProbability is the chance a submitted order is deliberately sized
	// to walk several price levels, modelling an aggressive institutional
	// order rather than routine retail flow.
	SweepProbability float64

	// NParticipants is the size of the synthetic trader pool orders are
	// randomly assigned to. This matters for a reason that has nothing to
	// do with realism for its own sake: the engine now enforces self-trade
	// prevention, so a generator that left every order at the zero-value
	// Owner would make the ENTIRE benchmark look like one participant
	// trading against itself -- STP would cancel most crossing orders
	// instead of matching them, and the measured throughput/latency would
	// reflect that pathology, not realistic exchange activity. A pool of
	// many distinct owners is what makes "realistic order flow" actually
	// true of this generator's output post-STP, not just pre-STP.
	NParticipants int
}

func DefaultParams(mid book.Price) Params {
	return Params{
		MidPrice:               mid,
		Tick:                   1,
		NParticipants:          200,
		PriceSpreadTicks:       40,
		CancelProbability:      0.42,
		MarketOrderProbability: 0.08,
		StopOrderProbability:   0.05,
		SweepProbability:       0.03,
	}
}

// Generator produces a deterministic Op stream from a seed. Determinism here
// matters for the same reason it matters in the matching engine itself: a
// benchmark result must be reproducible, or a regression can't be
// distinguished from workload noise between runs.
type Generator struct {
	p      Params
	rng    *rand.Rand
	nextID book.OrderID
	live   []book.OrderID
}

func New(p Params, seed int64) *Generator {
	return &Generator{p: p, rng: rand.New(rand.NewSource(seed))}
}

// clusteredPrice draws a price near the mid using a Laplace-like distribution
// (double-sided exponential), which concentrates mass near zero offset far
// more than a uniform or even normal draw -- matching how real limit orders
// pile up close to the touch and thin out quickly moving away from it.
func (g *Generator) clusteredPrice(side book.Side) book.Price {
	u := g.rng.Float64() - 0.5
	sign := 1.0
	if u < 0 {
		sign = -1.0
	}
	offset := -sign * float64(g.p.PriceSpreadTicks) / 4.0 * math.Log(1-2*math.Abs(u))
	ticks := book.Price(offset)

	// Bias buys below mid and sells above mid on average, which is what keeps
	// a two-sided book from immediately crossing itself into one big market
	// order.
	if side == book.Buy {
		ticks = -absPrice(ticks)
	} else {
		ticks = absPrice(ticks)
	}
	return g.p.MidPrice + ticks*g.p.Tick
}

func absPrice(p book.Price) book.Price {
	if p < 0 {
		return -p
	}
	return p
}

// size draws from a power-law-ish distribution: mostly small, occasionally
// large. Real order size distributions are heavy-tailed for the same
// structural reason city sizes and word frequencies are.
func (g *Generator) size(sweep bool) book.Qty {
	base := 1.0 / math.Pow(1-g.rng.Float64(), 1.0/1.6) // Pareto, alpha=1.6
	q := book.Qty(base)
	if q < 1 {
		q = 1
	}
	if q > 5000 {
		q = 5000
	}
	if sweep {
		q *= 20
	}
	return q
}

// Next produces one operation. The caller is responsible for feeding it to a
// book and, for OpSubmit, informing the generator whether the order rested
// (via Live/Untrack) so future cancels target real orders.
func (g *Generator) Next() Op {
	if len(g.live) > 0 && g.rng.Float64() < g.p.CancelProbability {
		i := g.rng.Intn(len(g.live))
		id := g.live[i]
		g.live[i] = g.live[len(g.live)-1]
		g.live = g.live[:len(g.live)-1]
		return Op{Kind: OpCancel, CancelID: id}
	}

	g.nextID++
	side := book.Side(g.rng.Intn(2))
	sweep := g.rng.Float64() < g.p.SweepProbability

	n := g.p.NParticipants
	if n < 1 {
		n = 1
	}
	o := book.Order{
		ID:    g.nextID,
		Owner: uint32(g.rng.Intn(n)),
		Side:  side,
		Qty:   g.size(sweep),
		TIF:   book.GTC,
	}

	switch r := g.rng.Float64(); {
	case r < g.p.MarketOrderProbability:
		o.Type = MarketOrderType()
	case r < g.p.MarketOrderProbability+g.p.StopOrderProbability:
		o.Type = StopOrderType()
		o.StopPx = g.clusteredPrice(side)
		o.Px = o.StopPx + tickOffset(side, g.p.Tick)
	default:
		o.Type = LimitOrderType()
		o.Px = g.clusteredPrice(side)
	}

	return Op{Kind: OpSubmit, Order: o}
}

// Track records that an order is now resting on the book, making it eligible
// for a future synthetic cancel. Call after a Submit that left a remainder.
func (g *Generator) Track(id book.OrderID) {
	g.live = append(g.live, id)
}

func tickOffset(side book.Side, tick book.Price) book.Price {
	if side == book.Buy {
		return tick * 2 // buy stop triggers upward, limit a bit further up
	}
	return -tick * 2
}

// Small indirection so this file doesn't need to import book's order-type
// constants under names that collide with this package's own vocabulary.
func LimitOrderType() book.OrdType     { return book.LimitOrder }
func MarketOrderType() book.OrdType    { return book.MarketOrder }
func StopOrderType() book.OrdType      { return book.StopLimitOrder }
