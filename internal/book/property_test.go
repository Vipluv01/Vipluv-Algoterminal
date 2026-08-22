package book

import (
	"math/rand"
	"testing"
)

// Randomised property testing, rather than more hand-written cases.
//
// Hand-written tests only cover the situations the author already thought of,
// and the bugs that matter in a matching engine live in the situations nobody
// thought of: a cancel that empties a level while a sweep is walking it, a stop
// that triggers into a book its own trigger emptied, a partial fill on the last
// order of the last level. Random flow plus invariants checked after every
// single operation finds those, and reports them on the operation that caused
// the damage rather than a million operations later.

type opKind uint8

const (
	opSubmit opKind = iota
	opCancel
)

type op struct {
	kind     opKind
	order    Order
	cancelID OrderID
}

// genScript builds a reproducible operation sequence. Everything is driven by
// the seed: no clock, no map iteration, no concurrency. Given the same seed the
// script is byte-identical, which is what makes the determinism test meaningful.
func genScript(seed int64, n int) []op {
	rng := rand.New(rand.NewSource(seed))
	ops := make([]op, 0, n)
	var nextID OrderID
	live := make([]OrderID, 0, n)

	for i := 0; i < n; i++ {
		// Cancels are common in real flow -- most orders never trade -- so the
		// script leans on them heavily to exercise unlink paths.
		if len(live) > 0 && rng.Intn(100) < 35 {
			j := rng.Intn(len(live))
			ops = append(ops, op{kind: opCancel, cancelID: live[j]})
			live = append(live[:j], live[j+1:]...)
			continue
		}

		nextID++
		o := Order{
			ID:    nextID,
			Owner: uint32(rng.Intn(8)),
			// Tight band around 10000 so orders actually cross rather than
			// resting harmlessly far apart.
			Px:   Price(9950 + rng.Intn(101)),
			Qty:  Qty(1 + rng.Intn(50)),
			Side: Side(rng.Intn(2)),
		}
		switch r := rng.Intn(100); {
		case r < 60:
			o.Type, o.TIF = LimitOrder, GTC
			live = append(live, o.ID)
		case r < 72:
			o.Type, o.TIF = LimitOrder, IOC
		case r < 78:
			o.Type, o.TIF = LimitOrder, FOK
		case r < 90:
			o.Type, o.TIF = MarketOrder, IOC
		default:
			o.Type, o.TIF = StopLimitOrder, GTC
			o.StopPx = Price(9950 + rng.Intn(101))
			live = append(live, o.ID)
		}
		ops = append(ops, op{kind: opSubmit, order: o})
	}
	return ops
}

// ledger tracks how much of each order has been submitted and filled, so the
// test can assert that the engine neither creates nor destroys quantity.
type ledger struct {
	submitted map[OrderID]Qty
	filled    map[OrderID]Qty
}

func newLedger() *ledger {
	return &ledger{submitted: map[OrderID]Qty{}, filled: map[OrderID]Qty{}}
}

func TestPropertyRandomFlow(t *testing.T) {
	const ops = 20000

	for _, seed := range []int64{1, 7, 42, 1337, 99991} {
		b, err := New(Config{MinPx: 1, MaxPx: 20000, Tick: 1, Capacity: 1 << 16})
		if err != nil {
			t.Fatal(err)
		}
		l := newLedger()
		minPx, maxPx := b.Band()

		for i, o := range genScript(seed, ops) {
			switch o.kind {
			case opCancel:
				b.Cancel(o.cancelID)
			case opSubmit:
				fills, r := b.Submit(o.order)
				if r == RejectNone {
					l.submitted[o.order.ID] = o.order.Qty
				}
				for _, f := range fills {
					if f.Qty <= 0 {
						t.Fatalf("seed %d op %d: fill with qty %d", seed, i, f.Qty)
					}
					if f.Px < minPx || f.Px > maxPx {
						t.Fatalf("seed %d op %d: fill priced %d outside band", seed, i, f.Px)
					}
					if f.TakerID == f.MakerID {
						t.Fatalf("seed %d op %d: order %d traded with itself", seed, i, f.TakerID)
					}
					l.filled[f.TakerID] += f.Qty
					l.filled[f.MakerID] += f.Qty
				}
			}

			// The whole point: verify after EVERY operation, so a failure names
			// the operation that broke it.
			if err := b.Check(); err != nil {
				t.Fatalf("seed %d: invariant broken at op %d: %v", seed, i, err)
			}
		}

		// Quantity conservation. An engine that fills more than was submitted
		// has invented inventory; one that loses quantity has silently dropped
		// a customer's order.
		for id, sub := range l.submitted {
			got := l.filled[id]
			if got > sub {
				t.Fatalf("seed %d: order %d submitted %d but filled %d", seed, id, sub, got)
			}
			if rem, live := b.Remaining(id); live && got+rem != sub {
				t.Fatalf("seed %d: order %d: filled %d + resting %d != submitted %d",
					seed, id, got, rem, sub)
			}
		}

		st := b.Stats()
		t.Logf("seed %-6d ops=%d trades=%d volume=%d resting=%d", seed, ops, st.Trades, st.Volume, st.LiveOrders)
	}
}

// Determinism is the property that makes everything else possible: crash
// recovery by replaying the log, reproducing a customer's disputed fill, and
// debugging a production incident offline. If the same input can produce two
// different outputs, none of that works. This runs one script through two
// independent books and demands byte-identical fill streams.
func TestDeterministicReplay(t *testing.T) {
	const ops = 50000
	script := genScript(20260905, ops)

	run := func() []Fill {
		b, err := New(Config{MinPx: 1, MaxPx: 20000, Tick: 1, Capacity: 1 << 16})
		if err != nil {
			t.Fatal(err)
		}
		var all []Fill
		for _, o := range script {
			switch o.kind {
			case opCancel:
				b.Cancel(o.cancelID)
			case opSubmit:
				f, _ := b.Submit(o.order)
				all = append(all, f...)
			}
		}
		return all
	}

	a, c := run(), run()
	if len(a) != len(c) {
		t.Fatalf("replay produced %d fills, original produced %d", len(c), len(a))
	}
	for i := range a {
		if a[i] != c[i] {
			t.Fatalf("replay diverged at fill %d:\n original %+v\n replay   %+v", i, a[i], c[i])
		}
	}
	t.Logf("%d fills reproduced exactly across independent runs", len(a))
}

func FuzzBook(f *testing.F) {
	f.Add(int64(1), 500)
	f.Add(int64(88), 2000)

	f.Fuzz(func(t *testing.T, seed int64, n int) {
		if n < 1 || n > 5000 {
			t.Skip()
		}
		b, err := New(Config{MinPx: 1, MaxPx: 20000, Tick: 1, Capacity: 1 << 14})
		if err != nil {
			t.Fatal(err)
		}
		for i, o := range genScript(seed, n) {
			switch o.kind {
			case opCancel:
				b.Cancel(o.cancelID)
			case opSubmit:
				b.Submit(o.order)
			}
			if err := b.Check(); err != nil {
				t.Fatalf("seed %d op %d: %v", seed, i, err)
			}
		}
	})
}
