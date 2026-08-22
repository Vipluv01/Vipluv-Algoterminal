package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"os/exec"
	"testing"
)

func TestSideRoundTrip(t *testing.T) {
	for _, s := range []string{"buy", "sell"} {
		if got := sideStr(sideOf(s)); got != s {
			t.Errorf("side round-trip: %q -> %q, want %q", s, got, s)
		}
	}
	// Anything unrecognized defaults to buy, deliberately -- documents the
	// choice so a future wire-format typo fails loudly via a wrong side
	// rather than a panic.
	if sideOf("garbage") != sideOf("buy") {
		t.Error("unrecognized side should default to buy")
	}
}

func TestTypeAndTIFDoNotPanic(t *testing.T) {
	for _, s := range []string{"limit", "market", "stop_limit", "unknown"} {
		_ = typeOf(s)
	}
	for _, s := range []string{"gtc", "ioc", "fok", "unknown"} {
		_ = tifOf(s)
	}
}

// TestBinaryProtocolEndToEnd builds the real binary and drives it exactly as
// the Python client will: as a subprocess, one JSON line in, one JSON line
// out. This is what actually exercises main()'s dispatch loop -- the
// unit-level tests above check the type-mapping helpers, but only this test
// proves the wire protocol itself works end to end.
func TestBinaryProtocolEndToEnd(t *testing.T) {
	bin := t.TempDir() + "/simserver"
	build := exec.Command("go", "build", "-o", bin, ".")
	if out, err := build.CombinedOutput(); err != nil {
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
	defer cmd.Process.Kill()

	reader := bufio.NewScanner(stdout)
	writeReq := func(req map[string]any) {
		b, _ := json.Marshal(req)
		var buf bytes.Buffer
		buf.Write(b)
		buf.WriteByte('\n')
		stdin.Write(buf.Bytes())
	}
	readLine := func() response {
		if !reader.Scan() {
			t.Fatalf("subprocess closed stdout unexpectedly: %v", reader.Err())
		}
		var r response
		if err := json.Unmarshal(reader.Bytes(), &r); err != nil {
			t.Fatalf("bad response json: %v (line: %s)", err, reader.Text())
		}
		return r
	}

	writeReq(map[string]any{
		"id": 1, "op": "new_book",
		"config": map[string]any{"min_px": 1, "max_px": 20000, "tick": 1, "capacity": 65536},
	})
	if r := readLine(); !r.OK {
		t.Fatalf("new_book failed: %+v", r)
	}

	writeReq(map[string]any{
		"id": 2, "op": "submit",
		"order": map[string]any{"id": 1, "owner": 1001, "px": 100, "qty": 10, "side": "buy", "type": "limit", "tif": "gtc"},
	})
	if r := readLine(); !r.OK || r.Reject != "none" {
		t.Fatalf("resting submit failed: %+v", r)
	}

	writeReq(map[string]any{
		"id": 3, "op": "submit",
		"order": map[string]any{"id": 2, "owner": 1002, "px": 100, "qty": 5, "side": "sell", "type": "limit", "tif": "gtc"},
	})
	if r := readLine(); !r.OK || len(r.Fills) != 1 || r.Fills[0].Qty != 5 || r.Fills[0].Px != 100 {
		t.Fatalf("crossing submit: expected one 5-qty fill at 100, got %+v", r)
	}

	writeReq(map[string]any{"id": 4, "op": "best_bid"})
	if r := readLine(); !r.Present || r.Px != 100 || r.Qty != 5 {
		t.Fatalf("best_bid after partial fill: got px=%d qty=%d present=%v", r.Px, r.Qty, r.Present)
	}

	writeReq(map[string]any{"id": 5, "op": "check"})
	if r := readLine(); !r.OK {
		t.Fatalf("invariant check failed over the wire: %s", r.Error)
	}

	writeReq(map[string]any{"id": 6, "op": "cancel", "cancel_id": 1})
	if r := readLine(); !r.OK || r.Reject != "none" {
		t.Fatalf("cancel failed: %+v", r)
	}

	writeReq(map[string]any{"id": 7, "op": "best_bid"})
	if r := readLine(); r.Present {
		t.Fatalf("book should be empty after cancelling the only resting order, got %+v", r)
	}
}

// TestMalformedRequestsDoNotCrashTheServer is a regression test for a real
// incident: a client-side bug sent a "cancel" with no cancel_id, which
// dereferenced a nil pointer and took the whole process down. A server that
// crashes on one bad request is not something a caller can build a reliable
// simulation loop against -- every op must reject bad input with an error
// response and keep running, never panic.
func TestMalformedRequestsDoNotCrashTheServer(t *testing.T) {
	bin := t.TempDir() + "/simserver"
	build := exec.Command("go", "build", "-o", bin, ".")
	if out, err := build.CombinedOutput(); err != nil {
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
	defer cmd.Process.Kill()

	reader := bufio.NewScanner(stdout)
	writeRaw := func(line string) {
		stdin.Write([]byte(line + "\n"))
	}
	readLine := func() response {
		if !reader.Scan() {
			t.Fatalf("subprocess closed stdout unexpectedly (likely crashed): %v", reader.Err())
		}
		var r response
		if err := json.Unmarshal(reader.Bytes(), &r); err != nil {
			t.Fatalf("bad response json: %v (line: %s)", err, reader.Text())
		}
		return r
	}

	// The exact incident: cancel with no cancel_id at all.
	writeRaw(`{"id": 1, "op": "cancel"}`)
	if r := readLine(); r.OK || r.Error == "" {
		t.Errorf("cancel with no id should error, not succeed: %+v", r)
	}

	// Every other op attempted before new_book -- must all error, not panic.
	for _, req := range []string{
		`{"id": 2, "op": "submit", "order": {"id": 1, "px": 100, "qty": 10, "side": "buy"}}`,
		`{"id": 3, "op": "best_bid"}`,
		`{"id": 4, "op": "depth"}`,
		`{"id": 5, "op": "check"}`,
	} {
		writeRaw(req)
		if r := readLine(); r.OK {
			t.Errorf("op before new_book should error, not succeed: req=%s resp=%+v", req, r)
		}
	}

	// submit with no order field.
	writeRaw(`{"id": 6, "op": "new_book", "config": {"min_px": 1, "max_px": 20000, "tick": 1, "capacity": 1024}}`)
	if r := readLine(); !r.OK {
		t.Fatalf("new_book should succeed: %+v", r)
	}
	writeRaw(`{"id": 7, "op": "submit"}`)
	if r := readLine(); r.OK || r.Error == "" {
		t.Errorf("submit with no order should error, not succeed: %+v", r)
	}

	// The process must still be ALIVE and responsive after all of the above.
	writeRaw(`{"id": 8, "op": "stats"}`)
	if r := readLine(); !r.OK {
		t.Fatalf("server should still be alive and responsive after malformed requests: %+v", r)
	}
}

// TestCrashRecoveryThroughTheWireProtocol is the actual end-to-end proof:
// start a real subprocess, submit orders with wal_path configured, kill the
// process WITHOUT a clean shutdown (SIGKILL, not a graceful close -- this
// is the honest simulation of a crash, not a cooperative exit), start a
// SECOND independent subprocess pointed at the same wal_path, and confirm
// its recovered book state matches what the first process had before it
// died. internal/wal already proves the replay mechanism is correct in
// isolation; this proves the wiring into the actual server process is
// correct too, under conditions a real crash would produce.
func TestCrashRecoveryThroughTheWireProtocol(t *testing.T) {
	bin := t.TempDir() + "/simserver"
	build := exec.Command("go", "build", "-o", bin, ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build failed: %v\n%s", err, out)
	}
	walPath := t.TempDir() + "/crash_test.wal"

	startProcess := func() (*exec.Cmd, func(map[string]any), func() response) {
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
		reader := bufio.NewScanner(stdout)
		writeReq := func(req map[string]any) {
			b, _ := json.Marshal(req)
			var buf bytes.Buffer
			buf.Write(b)
			buf.WriteByte('\n')
			stdin.Write(buf.Bytes())
		}
		readLine := func() response {
			if !reader.Scan() {
				t.Fatalf("subprocess closed stdout unexpectedly: %v", reader.Err())
			}
			var r response
			json.Unmarshal(reader.Bytes(), &r)
			return r
		}
		return cmd, writeReq, readLine
	}

	// --- Process 1: submit orders, then die ungracefully ---
	cmd1, write1, read1 := startProcess()

	write1(map[string]any{
		"id": 1, "op": "new_book",
		"config": map[string]any{
			"min_px": 1, "max_px": 20000, "tick": 1, "capacity": 65536,
			"wal_path": walPath,
		},
	})
	if r := read1(); !r.OK {
		t.Fatalf("process 1 new_book failed: %+v", r)
	}

	write1(map[string]any{
		"id": 2, "op": "submit",
		"order": map[string]any{"id": 1, "owner": 1, "px": 100, "qty": 10, "side": "buy", "type": "limit", "tif": "gtc"},
	})
	if r := read1(); !r.OK || r.Reject != "none" {
		t.Fatalf("process 1 submit 1 failed: %+v", r)
	}

	write1(map[string]any{
		"id": 3, "op": "submit",
		"order": map[string]any{"id": 2, "owner": 2, "px": 99, "qty": 5, "side": "buy", "type": "limit", "tif": "gtc"},
	})
	if r := read1(); !r.OK {
		t.Fatalf("process 1 submit 2 failed: %+v", r)
	}

	// A cancel too, so recovery must correctly NOT resurrect this order.
	write1(map[string]any{
		"id": 4, "op": "submit",
		"order": map[string]any{"id": 3, "owner": 3, "px": 98, "qty": 7, "side": "buy", "type": "limit", "tif": "gtc"},
	})
	read1()
	write1(map[string]any{"id": 5, "op": "cancel", "cancel_id": 3})
	if r := read1(); !r.OK || r.Reject != "none" {
		t.Fatalf("process 1 cancel failed: %+v", r)
	}

	// The crash: SIGKILL, not Process.Kill()-then-wait-cleanly -- no
	// graceful shutdown hook runs. If recovery only worked because of
	// clean-exit behavior, this is what would expose it.
	if err := cmd1.Process.Kill(); err != nil {
		t.Fatalf("kill process 1: %v", err)
	}
	cmd1.Wait()

	// --- Process 2: independent process, same wal_path, must recover ---
	cmd2, write2, read2 := startProcess()
	defer cmd2.Process.Kill()

	write2(map[string]any{
		"id": 1, "op": "new_book",
		"config": map[string]any{
			"min_px": 1, "max_px": 20000, "tick": 1, "capacity": 65536,
			"wal_path": walPath,
		},
	})
	r := read2()
	if !r.OK {
		t.Fatalf("process 2 recovery new_book failed: %+v", r)
	}
	if r.Recovered != 4 {
		t.Fatalf("want 4 entries recovered (3 submits + 1 cancel), got %d", r.Recovered)
	}

	write2(map[string]any{"id": 2, "op": "best_bid"})
	if bb := read2(); !bb.Present || bb.Px != 100 || bb.Qty != 10 {
		t.Fatalf("recovered best bid should be 100x10 (order 1), got px=%d qty=%d present=%v",
			bb.Px, bb.Qty, bb.Present)
	}

	write2(map[string]any{"id": 3, "op": "depth", "depth": 10})
	depthResp := read2()
	if len(depthResp.Bids) != 2 {
		t.Fatalf("want 2 resting bids (orders 1 and 2; order 3 was cancelled), got %d: %+v",
			len(depthResp.Bids), depthResp.Bids)
	}

	write2(map[string]any{"id": 4, "op": "check"})
	if r := read2(); !r.OK {
		t.Fatalf("recovered book invariant check failed: %s", r.Error)
	}

	// The recovered process must keep working normally afterward -- new
	// orders on top of recovered state, not just a read-only snapshot.
	write2(map[string]any{
		"id": 5, "op": "submit",
		"order": map[string]any{"id": 4, "owner": 4, "px": 100, "qty": 10, "side": "sell", "type": "limit", "tif": "gtc"},
	})
	if r := read2(); !r.OK || len(r.Fills) != 1 || r.Fills[0].MakerID != 1 {
		t.Fatalf("recovered book should still match normally against recovered order 1, got %+v", r)
	}
}
