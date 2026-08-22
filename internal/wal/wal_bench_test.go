package wal

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/vipluv/bourse/internal/book"
)

// Quantifies the real cost of fsync-per-write durability, rather than
// leaving it as an assumed "this is probably slow" -- a number here is
// worth more than an intuition, same discipline as everything else
// benchmarked in this project.
func BenchmarkWriterLogSubmitWithFsync(b *testing.B) {
	dir := b.TempDir()
	w, err := Create(filepath.Join(dir, "bench.wal"))
	if err != nil {
		b.Fatal(err)
	}
	defer w.Close()

	o := book.Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: book.Buy, TIF: book.GTC}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		o.ID = book.OrderID(i)
		if err := w.LogSubmit(o); err != nil {
			b.Fatal(err)
		}
	}
}

// The counterfactual: buffered writes with NO fsync, to isolate exactly
// how much of the cost above is the durability guarantee itself versus
// JSON encoding overhead. This pattern is intentionally NOT what Writer
// does -- it exists only to isolate the fsync cost for the README, and
// must never be used for a real log (a crash before the OS flushes its
// buffer loses these entries silently, defeating the entire point of a WAL).
func BenchmarkWriterLogSubmitNoFsyncForComparisonOnly(b *testing.B) {
	dir := b.TempDir()
	f, err := os.OpenFile(filepath.Join(dir, "bench_nosync.wal"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		b.Fatal(err)
	}
	defer f.Close()
	buf := bufio.NewWriter(f)

	o := book.Order{ID: 1, Owner: 1, Px: 100, Qty: 10, Side: book.Buy, TIF: book.GTC}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		o.ID = book.OrderID(i)
		data, err := json.Marshal(fromSubmit(o))
		if err != nil {
			b.Fatal(err)
		}
		buf.Write(data)
		buf.WriteByte('\n')
	}
	buf.Flush()
}
