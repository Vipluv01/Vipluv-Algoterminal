package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"os/exec"
	"testing"
)

// wireSession is a live simserver subprocess plus the two helpers every
// protocol test needs. The existing tests each rebuild this inline; the new
// ops below exercise enough of the protocol that sharing it is worth it.
type wireSession struct {
	t      *testing.T
	stdin  io.WriteCloser
	reader *bufio.Scanner
	cmd    *exec.Cmd
}

func startServer(t *testing.T) *wireSession {
	t.Helper()

	bin := t.TempDir() + "/simserver"
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("build failed: %v\n%s", err, out)
	}

	cmd := exec.Command(bin)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatalf("start: %v", err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill() })

	return &wireSession{t: t, stdin: stdin, reader: bufio.NewScanner(stdout), cmd: cmd}
}

func (s *wireSession) do(req map[string]any) response {
	s.t.Helper()
	b, _ := json.Marshal(req)
	var buf bytes.Buffer
	buf.Write(b)
	buf.WriteByte('\n')
	if _, err := s.stdin.Write(buf.Bytes()); err != nil {
		s.t.Fatalf("write request: %v", err)
	}
	if !s.reader.Scan() {
		s.t.Fatalf("subprocess closed stdout unexpectedly (likely crashed): %v", s.reader.Err())
	}
	var r response
	if err := json.Unmarshal(s.reader.Bytes(), &r); err != nil {
		s.t.Fatalf("bad response json: %v (line: %s)", err, s.reader.Text())
	}
	return r
}

func (s *wireSession) newBook() {
	s.t.Helper()
	r := s.do(map[string]any{
		"id": 1, "op": "new_book",
		"config": map[string]any{"min_px": 1, "max_px": 20000, "tick": 1, "capacity": 65536},
	})
	if !r.OK {
		s.t.Fatalf("new_book failed: %+v", r)
	}
}

func (s *wireSession) submit(id, owner, px, qty int, side string) response {
	s.t.Helper()
	return s.do(map[string]any{
		"id": id + 100, "op": "submit",
		"order": map[string]any{
			"id": id, "owner": owner, "px": px, "qty": qty,
			"side": side, "type": "limit", "tif": "gtc",
		},
	})
}

// TestMidSpreadLastPxOverTheWire covers three Book methods that existed but
// were unreachable from Python, which had to reconstruct mid from
// best_bid/best_ask -- a second definition of the same quantity, free to
// drift from the engine's own.
func TestMidSpreadLastPxOverTheWire(t *testing.T) {
	s := startServer(t)
	s.newBook()

	// Empty book: all three must report absence rather than a bogus zero.
	for _, op := range []string{"mid", "spread", "last_px"} {
		if r := s.do(map[string]any{"id": 2, "op": op}); !r.OK || r.Present {
			t.Errorf("%s on an empty book should be ok-but-not-present, got %+v", op, r)
		}
	}

	s.submit(1, 1001, 100, 10, "buy")
	s.submit(2, 1002, 106, 10, "sell")

	// Mid truncates: (100+106)/2 = 103 exactly, but the integer division is
	// the point -- see Engine.mid() in sim/bourse_sim/engine.py for why the
	// Python client keeps its own fractional version alongside this one.
	if r := s.do(map[string]any{"id": 3, "op": "mid"}); !r.Present || r.Value != 103 {
		t.Errorf("mid: want 103 present, got value=%d present=%v", r.Value, r.Present)
	}
	if r := s.do(map[string]any{"id": 4, "op": "spread"}); !r.Present || r.Value != 6 {
		t.Errorf("spread: want 6 ticks, got value=%d present=%v", r.Value, r.Present)
	}

	// Nothing has traded yet, so last_px must still be absent -- it is
	// distinct from mid, which is derived from resting quotes.
	if r := s.do(map[string]any{"id": 5, "op": "last_px"}); r.Present {
		t.Errorf("last_px before any trade should be absent, got %+v", r)
	}

	// Cross the book: a sell at 100 hits the resting bid.
	s.submit(3, 1003, 100, 4, "sell")
	if r := s.do(map[string]any{"id": 6, "op": "last_px"}); !r.Present || r.Value != 100 {
		t.Errorf("last_px after a trade at 100: got value=%d present=%v", r.Value, r.Present)
	}
}

// TestMidTruncatesRatherThanRounding pins the semantic the Python client
// documents and depends on. An odd-width book is the case where the two
// definitions actually differ.
func TestMidTruncatesRatherThanRounding(t *testing.T) {
	s := startServer(t)
	s.newBook()

	s.submit(1, 1001, 100, 10, "buy")
	s.submit(2, 1002, 101, 10, "sell")

	// (100+101)/2 is 100.5; integer division gives 100, NOT 101.
	r := s.do(map[string]any{"id": 3, "op": "mid"})
	if !r.Present || r.Value != 100 {
		t.Errorf("mid of a 100/101 book must truncate to 100, got value=%d present=%v", r.Value, r.Present)
	}
}

// TestRemainingDistinguishesZeroFromAbsent is the reason the wire protocol
// carries a separate `present` flag instead of overloading a zero value.
// "resting with nothing left" and "not on the book at all" are different
// answers, and a caller tracking a GTC order needs to tell them apart.
func TestRemainingDistinguishesZeroFromAbsent(t *testing.T) {
	s := startServer(t)
	s.newBook()

	// An order that was never submitted.
	if r := s.do(map[string]any{"id": 2, "op": "remaining", "order_id": 999}); !r.OK || r.Present {
		t.Errorf("remaining for an unknown order should be ok-but-not-present, got %+v", r)
	}

	s.submit(1, 1001, 100, 10, "buy")
	if r := s.do(map[string]any{"id": 3, "op": "remaining", "order_id": 1}); !r.Present || r.Value != 10 {
		t.Errorf("remaining for a fully-resting order: want 10, got value=%d present=%v", r.Value, r.Present)
	}

	// Partially fill it: 4 of 10 taken.
	s.submit(2, 1002, 100, 4, "sell")
	if r := s.do(map[string]any{"id": 4, "op": "remaining", "order_id": 1}); !r.Present || r.Value != 6 {
		t.Errorf("remaining after a 4-qty fill: want 6, got value=%d present=%v", r.Value, r.Present)
	}

	// Fill the rest: the order leaves the book entirely.
	s.submit(3, 1003, 100, 6, "sell")
	if r := s.do(map[string]any{"id": 5, "op": "remaining", "order_id": 1}); r.Present {
		t.Errorf("a fully-filled order should be absent from the book, got %+v", r)
	}
}

// TestStatsReportsSTPCancels covers a counter the engine tracked but the
// wire protocol dropped: the `stats` op simply never copied it into the
// response, so the one number saying whether self-trade prevention is doing
// anything was invisible to every caller.
func TestStatsReportsSTPCancels(t *testing.T) {
	s := startServer(t)
	s.newBook()

	if r := s.do(map[string]any{"id": 2, "op": "stats"}); !r.OK || r.STPCancels != 0 {
		t.Errorf("fresh book should report 0 stp_cancels, got %+v", r)
	}

	// Same owner on both sides: the resting order is cancelled by STP, not filled.
	s.submit(1, 7777, 100, 10, "buy")
	s.submit(2, 7777, 100, 10, "sell")

	r := s.do(map[string]any{"id": 3, "op": "stats"})
	if !r.OK {
		t.Fatalf("stats failed: %+v", r)
	}
	if r.STPCancels != 1 {
		t.Errorf("a same-owner cross should record exactly 1 stp cancel, got %d", r.STPCancels)
	}
	if r.Trades != 0 {
		t.Errorf("self-trade prevention must not produce a trade, got trades=%d", r.Trades)
	}
}

// TestNewQueryOpsRejectMalformedInput extends the hardening established by
// TestMalformedRequestsDoNotCrashTheServer to the ops added here. The
// original incident was a nil-pointer deref on a missing field; every new op
// that reads an optional pointer has to be checked the same way.
func TestNewQueryOpsRejectMalformedInput(t *testing.T) {
	s := startServer(t)

	// Before new_book, every new op must error rather than panic.
	for _, op := range []string{"mid", "spread", "last_px", "remaining"} {
		if r := s.do(map[string]any{"id": 1, "op": op}); r.OK {
			t.Errorf("%s before new_book should error, not succeed: %+v", op, r)
		}
	}

	s.newBook()

	// remaining with no order_id: the exact shape of the original crash.
	if r := s.do(map[string]any{"id": 2, "op": "remaining"}); r.OK || r.Error == "" {
		t.Errorf("remaining with no order_id should error, not succeed: %+v", r)
	}

	// And the process must still be alive.
	if r := s.do(map[string]any{"id": 3, "op": "stats"}); !r.OK {
		t.Fatalf("server should still be responsive after malformed requests: %+v", r)
	}
}
