package wal

import (
	"math/rand"
	"os"
	"path/filepath"
	"testing"

	"github.com/vipluv/bourse/internal/book"
)

func newBook(t *testing.T) *book.Book {
	t.Helper()
	b, err := book.New(book.Config{MinPx: 1, MaxPx: 20000, Tick: 1, Capacity: 1 << 16})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return b
}

func TestWriteAndReplayReconstructsRestingOrders(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "test.wal")

	w, err := Create(path)
	if err != nil {
		t.Fatalf("Create: %v", err)
	}

	live := newBook(t)
	o1 := book.Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: book.Buy, TIF: book.GTC}
	if _, r := live.Submit(o1); r != book.RejectNone {
		t.Fatalf("submit 1: %s", r)
	}
	if err := w.LogSubmit(o1); err != nil {
		t.Fatalf("LogSubmit: %v", err)
	}

	o2 := book.Order{ID: 2, Owner: 2, Px: 100, Qty: 5, Side: book.Buy, TIF: book.GTC}
	if _, r := live.Submit(o2); r != book.RejectNone {
		t.Fatalf("submit 2: %s", r)
	}
	if err := w.LogSubmit(o2); err != nil {
		t.Fatalf("LogSubmit: %v", err)
	}

	if r := live.Cancel(1); r != book.RejectNone {
		t.Fatalf("cancel 1: %s", r)
	}
	if err := w.LogCancel(1); err != nil {
		t.Fatalf("LogCancel: %v", err)
	}
	w.Close()

	recovered := newBook(t)
	n, err := Replay(path, recovered)
	if err != nil {
		t.Fatalf("Replay: %v", err)
	}
	if n != 3 {
		t.Fatalf("want 3 entries replayed, got %d", n)
	}

	if err := recovered.Check(); err != nil {
		t.Fatalf("recovered book invariants: %v", err)
	}
	if _, live1 := recovered.Remaining(1); live1 {
		t.Error("order 1 was cancelled -- should not be live after recovery")
	}
	if rem, live2 := recovered.Remaining(2); !live2 || rem != 5 {
		t.Errorf("order 2 should be live with qty 5, got live=%v rem=%d", live2, rem)
	}
}

func TestReplayOfMissingLogIsNotAnError(t *testing.T) {
	// A fresh book with no prior crash has no log yet -- that is the normal
	// startup case, not a failure.
	recovered := newBook(t)
	n, err := Replay(filepath.Join(t.TempDir(), "does-not-exist.wal"), recovered)
	if err != nil {
		t.Fatalf("missing log should not error, got: %v", err)
	}
	if n != 0 {
		t.Errorf("want 0 entries, got %d", n)
	}
}

func TestReplayDetectsCorruptEntry(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "corrupt.wal")
	if err := os.WriteFile(path, []byte("{not valid json\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	recovered := newBook(t)
	_, err := Replay(path, recovered)
	if err == nil {
		t.Fatal("corrupt entry should produce an error, not silently succeed")
	}
}

func TestReplayDetectsDivergedBookConfig(t *testing.T) {
	// Log an order priced at 100 against one Book, then try to replay it
	// into a Book whose price band doesn't include 100 -- this must fail
	// loudly, not silently reconstruct a wrong state.
	dir := t.TempDir()
	path := filepath.Join(dir, "test.wal")

	live := newBook(t)
	o := book.Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: book.Buy, TIF: book.GTC}
	live.Submit(o)

	w, _ := Create(path)
	w.LogSubmit(o)
	w.Close()

	wrongBand, err := book.New(book.Config{MinPx: 200, MaxPx: 300, Tick: 1, Capacity: 1024})
	if err != nil {
		t.Fatal(err)
	}
	_, err = Replay(path, wrongBand)
	if err == nil {
		t.Fatal("replaying against a mismatched book config should error, not silently diverge")
	}
}

// This is the actual proof the feature works: drive a live book through
// thousands of randomized operations (same style as internal/book's own
// property test) while logging every accepted one, then replay the log
// into a completely independent fresh book and assert the two end up
// EXACTLY identical -- not "close", not "similar stats", identical resting
// orders and identical structural invariants. This is only possible
// because internal/book is already proven deterministic
// (TestDeterministicReplay); this test is the WAL-specific half of that
// same guarantee.
func TestReplayReconstructsIdenticalStateAfterRandomizedSession(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "session.wal")

	live := newBook(t)
	w, err := Create(path)
	if err != nil {
		t.Fatal(err)
	}

	rng := rand.New(rand.NewSource(7))
	var nextID book.OrderID
	var liveIDs []book.OrderID

	const ops = 5000
	for i := 0; i < ops; i++ {
		if len(liveIDs) > 0 && rng.Intn(100) < 35 {
			idx := rng.Intn(len(liveIDs))
			id := liveIDs[idx]
			liveIDs = append(liveIDs[:idx], liveIDs[idx+1:]...)
			if r := live.Cancel(id); r == book.RejectNone {
				if err := w.LogCancel(id); err != nil {
					t.Fatal(err)
				}
			}
			continue
		}

		nextID++
		o := book.Order{
			ID:    nextID,
			Owner: uint32(rng.Intn(50)),
			Px:    book.Price(9950 + rng.Intn(101)),
			Qty:   book.Qty(1 + rng.Intn(50)),
			Side:  book.Side(rng.Intn(2)),
			Type:  book.LimitOrder,
			TIF:   book.GTC,
		}
		_, r := live.Submit(o)
		if r == book.RejectNone {
			if err := w.LogSubmit(o); err != nil {
				t.Fatal(err)
			}
			liveIDs = append(liveIDs, o.ID)
		}
	}
	w.Close()

	if err := live.Check(); err != nil {
		t.Fatalf("live book invariants before recovery: %v", err)
	}

	recovered := newBook(t)
	n, err := Replay(path, recovered)
	if err != nil {
		t.Fatalf("Replay: %v", err)
	}
	t.Logf("replayed %d log entries", n)

	if err := recovered.Check(); err != nil {
		t.Fatalf("recovered book invariants: %v", err)
	}

	liveStats, recoveredStats := live.Stats(), recovered.Stats()
	if liveStats.Trades != recoveredStats.Trades {
		t.Errorf("trade count diverged: live=%d recovered=%d", liveStats.Trades, recoveredStats.Trades)
	}
	if liveStats.Volume != recoveredStats.Volume {
		t.Errorf("volume diverged: live=%d recovered=%d", liveStats.Volume, recoveredStats.Volume)
	}
	if liveStats.LiveOrders != recoveredStats.LiveOrders {
		t.Errorf("live order count diverged: live=%d recovered=%d", liveStats.LiveOrders, recoveredStats.LiveOrders)
	}

	liveBid, _, liveOK := live.BestBid()
	recBid, _, recOK := recovered.BestBid()
	if liveOK != recOK || liveBid != recBid {
		t.Errorf("best bid diverged: live=(%d,%v) recovered=(%d,%v)", liveBid, liveOK, recBid, recOK)
	}
	liveAsk, _, liveOK2 := live.BestAsk()
	recAsk, _, recOK2 := recovered.BestAsk()
	if liveOK2 != recOK2 || liveAsk != recAsk {
		t.Errorf("best ask diverged: live=(%d,%v) recovered=(%d,%v)", liveAsk, liveOK2, recAsk, recOK2)
	}

	// Full depth comparison, not just the touch -- every resting price
	// level must match exactly.
	liveBids, liveAsks := live.Depth(1000)
	recBids, recAsks := recovered.Depth(1000)
	if len(liveBids) != len(recBids) || len(liveAsks) != len(recAsks) {
		t.Fatalf("depth level counts diverged: live bids=%d asks=%d, recovered bids=%d asks=%d",
			len(liveBids), len(liveAsks), len(recBids), len(recAsks))
	}
	for i := range liveBids {
		if liveBids[i] != recBids[i] {
			t.Errorf("bid level %d diverged: live=%+v recovered=%+v", i, liveBids[i], recBids[i])
		}
	}
	for i := range liveAsks {
		if liveAsks[i] != recAsks[i] {
			t.Errorf("ask level %d diverged: live=%+v recovered=%+v", i, liveAsks[i], recAsks[i])
		}
	}
}
