// Package wal implements crash recovery for a book.Book via a write-ahead
// log of accepted operations, replayed into a fresh book to reconstruct
// state after a restart.
//
// This works, and works simply, only because of a property internal/book
// already proves: TestDeterministicReplay shows that feeding the same
// sequence of operations into two independent Book instances produces
// byte-identical fills, every time -- no wall-clock reads, no map
// iteration, no concurrency inside the book itself. That means recovery
// does not need periodic snapshots, checksummed page writes, or any of the
// machinery a non-deterministic system would require: logging every
// accepted operation's INPUTS (never the resulting fills, which are
// derived, not stored) and replaying them from empty is sufficient and
// correct by construction. A snapshot would only ever be an optimization
// to avoid replaying a long log, never a correctness requirement.
//
// What is logged: only operations the book actually ACCEPTED. A submit
// that was rejected (bad price, duplicate id, FOK unfillable) never
// touched book state, so logging it would be replaying a no-op -- harmless
// for correctness, but every rejected operation the caller retries would
// bloat the log for zero benefit.
package wal

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"

	"github.com/vipluv/bourse/internal/book"
)

// EntryKind distinguishes the two operation types the book accepts.
type EntryKind uint8

const (
	EntrySubmit EntryKind = iota
	EntryCancel
)

// Entry is one write-ahead log record: enough to reconstruct the exact
// Submit or Cancel call that produced it. Fields are named and typed to
// marshal predictably to JSON -- one entry per line (JSONL), not a single
// JSON array, so a log can be appended to and read incrementally without
// ever parsing the whole file to add one record.
type Entry struct {
	Kind EntryKind `json:"kind"`

	// Submit fields (zero-valued and ignored for EntryCancel).
	ID     book.OrderID    `json:"id,omitempty"`
	Owner  uint32          `json:"owner,omitempty"`
	Px     book.Price      `json:"px,omitempty"`
	StopPx book.Price      `json:"stop_px,omitempty"`
	Qty    book.Qty        `json:"qty,omitempty"`
	Side   book.Side       `json:"side,omitempty"`
	Type   book.OrdType    `json:"type,omitempty"`
	TIF    book.TimeInForce `json:"tif,omitempty"`

	// Cancel field.
	CancelID book.OrderID `json:"cancel_id,omitempty"`
}

func (e Entry) toOrder() book.Order {
	return book.Order{
		ID: e.ID, Owner: e.Owner, Px: e.Px, StopPx: e.StopPx,
		Qty: e.Qty, Side: e.Side, Type: e.Type, TIF: e.TIF,
	}
}

func fromSubmit(o book.Order) Entry {
	return Entry{
		Kind: EntrySubmit, ID: o.ID, Owner: o.Owner, Px: o.Px, StopPx: o.StopPx,
		Qty: o.Qty, Side: o.Side, Type: o.Type, TIF: o.TIF,
	}
}

func fromCancel(id book.OrderID) Entry {
	return Entry{Kind: EntryCancel, CancelID: id}
}

// Writer appends accepted operations to a log file, fsync'ing after every
// write. fsync per entry, not batched, is a deliberate durability choice:
// a WAL whose whole point is surviving a crash is not allowed to lose the
// last few entries to an OS write-buffer that a crash caught before it
// flushed -- that would defeat the feature while looking like it worked in
// every test that doesn't inject a crash at exactly that moment.
type Writer struct {
	f   *os.File
	buf *bufio.Writer
}

func Create(path string) (*Writer, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, fmt.Errorf("wal: create %s: %w", path, err)
	}
	return &Writer{f: f, buf: bufio.NewWriter(f)}, nil
}

func (w *Writer) write(e Entry) error {
	b, err := json.Marshal(e)
	if err != nil {
		return fmt.Errorf("wal: marshal entry: %w", err)
	}
	if _, err := w.buf.Write(b); err != nil {
		return err
	}
	if err := w.buf.WriteByte('\n'); err != nil {
		return err
	}
	if err := w.buf.Flush(); err != nil {
		return fmt.Errorf("wal: flush: %w", err)
	}
	if err := w.f.Sync(); err != nil {
		return fmt.Errorf("wal: fsync: %w", err)
	}
	return nil
}

// LogSubmit records an accepted submit. Call AFTER Book.Submit returns
// RejectNone -- logging a rejected order would replay a no-op, and logging
// BEFORE the call risks recording an order that then fails validation and
// never actually entered the book, corrupting replay with an operation
// that never really happened.
func (w *Writer) LogSubmit(o book.Order) error { return w.write(fromSubmit(o)) }

// LogCancel records an accepted cancel. Same ordering rule: call after
// Book.Cancel returns RejectNone.
func (w *Writer) LogCancel(id book.OrderID) error { return w.write(fromCancel(id)) }

func (w *Writer) Close() error {
	if err := w.buf.Flush(); err != nil {
		return err
	}
	return w.f.Close()
}

// Replay reads every entry in the log at path and re-submits it, in order,
// into bk. bk should be freshly constructed (empty) -- Replay does not
// clear existing state first, so replaying into a non-empty book merges
// the log's history on top of whatever was already there, which is only
// correct if that is specifically what's wanted.
//
// Returns the number of entries replayed. An entry that fails to reapply
// (a Reject where the original operation succeeded) means the log and the
// book's configuration have diverged -- e.g. replaying against a Book
// constructed with a different price band or capacity than the one that
// generated the log -- and Replay stops immediately rather than silently
// continuing with a partially-reconstructed book.
func Replay(path string, bk *book.Book) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		if os.IsNotExist(err) {
			return 0, nil // no log yet is not an error: a fresh book has nothing to recover
		}
		return 0, fmt.Errorf("wal: open %s: %w", path, err)
	}
	defer f.Close()

	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	n := 0
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var e Entry
		if err := json.Unmarshal(line, &e); err != nil {
			return n, fmt.Errorf("wal: corrupt entry at line %d: %w", n+1, err)
		}

		switch e.Kind {
		case EntrySubmit:
			if _, reject := bk.Submit(e.toOrder()); reject != book.RejectNone {
				return n, fmt.Errorf("wal: replay diverged at entry %d: submit of order %d "+
					"rejected (%s) but the original was accepted (or the log is corrupt/out of "+
					"order) -- check the book was constructed with the same Config as the run "+
					"that produced this log", n+1, e.ID, reject)
			}
		case EntryCancel:
			if reject := bk.Cancel(e.CancelID); reject != book.RejectNone {
				return n, fmt.Errorf("wal: replay diverged at entry %d: cancel of order %d "+
					"rejected (%s) but the original was accepted", n+1, e.CancelID, reject)
			}
		default:
			return n, fmt.Errorf("wal: unknown entry kind %d at line %d", e.Kind, n+1)
		}
		n++
	}
	if err := sc.Err(); err != nil {
		return n, fmt.Errorf("wal: scan error after %d entries: %w", n, err)
	}
	return n, nil
}
