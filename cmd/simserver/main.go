// Command simserver exposes internal/book over a newline-delimited JSON
// protocol on stdin/stdout, so a process in any language can drive the
// matching engine without binding to Go directly.
//
// Why a subprocess protocol rather than cgo or a rewrite: the engine's value
// is that it's tested and benchmarked as-is (internal/book, unchanged by
// this file). The market simulation and market-maker logic built on top are
// new code, written in Python -- deliberately, since that's the language the
// rest of this analysis lives in. A line-based JSON protocol over stdio is
// the simplest boundary that keeps the Go core untouched: no CGo build
// complexity, no network port to manage, and it composes trivially with
// Python's subprocess module (one process, two pipes).
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"

	"github.com/vipluv/bourse/internal/book"
	"github.com/vipluv/bourse/internal/wal"
)

type request struct {
	ID     int             `json:"id"`
	Op     string          `json:"op"`
	Config *wireConfig     `json:"config,omitempty"`
	Order  *wireOrder      `json:"order,omitempty"`
	Cancel *wireOrderID    `json:"cancel_id,omitempty"`
	Depth  *int            `json:"depth,omitempty"`
	Owner  *uint32         `json:"owner,omitempty"`    // "position" op only
	OrderID *wireOrderID   `json:"order_id,omitempty"` // "remaining" op only
}

type wireConfig struct {
	MinPx    int64   `json:"min_px"`
	MaxPx    int64   `json:"max_px"`
	Tick     int64   `json:"tick"`
	Capacity int     `json:"capacity"`
	WalPath  *string `json:"wal_path,omitempty"` // if set, new_book replays any
	                                                // existing log at this path
	                                                // before accepting new orders
	                                                // (crash recovery), then logs
	                                                // every accepted submit/cancel
	                                                // here going forward.
	PriceCollarBps int64 `json:"price_collar_bps,omitempty"` // 0 disables; see
	                                                            // book.Config's doc comment
	PositionLimit  int64 `json:"position_limit,omitempty"`   // 0 disables; see
	                                                            // book.Config's doc comment
}

type wireOrder struct {
	ID     uint64 `json:"id"`
	Owner  uint32 `json:"owner"`
	Px     int64  `json:"px"`
	StopPx int64  `json:"stop_px"`
	Qty    int64  `json:"qty"`
	Side   string `json:"side"`   // "buy" | "sell"
	Type   string `json:"type"`   // "limit" | "market" | "stop_limit"
	TIF    string `json:"tif"`    // "gtc" | "ioc" | "fok"
}

type wireOrderID = uint64

type wireFill struct {
	Seq       uint64 `json:"seq"`
	TakerID   uint64 `json:"taker_id"`
	MakerID   uint64 `json:"maker_id"`
	Px        int64  `json:"px"`
	Qty       int64  `json:"qty"`
	TakerSide string `json:"taker_side"`
}

type wirePriceLevel struct {
	Px    int64 `json:"px"`
	Qty   int64 `json:"qty"`
	Count int32 `json:"count"`
}

type response struct {
	ID       int              `json:"id"`
	OK       bool             `json:"ok"`
	Error    string           `json:"error,omitempty"`
	Fills    []wireFill       `json:"fills,omitempty"`
	Reject   string           `json:"reject,omitempty"`
	Px       int64            `json:"px,omitempty"`
	Qty      int64            `json:"qty,omitempty"`
	Present  bool             `json:"present,omitempty"`
	Bids     []wirePriceLevel `json:"bids,omitempty"`
	Asks     []wirePriceLevel `json:"asks,omitempty"`
	Trades   uint64           `json:"trades,omitempty"`
	Volume   int64            `json:"volume,omitempty"`
	Live     int              `json:"live_orders,omitempty"`
	Sequence uint64           `json:"sequence,omitempty"`
	Recovered int             `json:"recovered,omitempty"` // new_book only: WAL entries replayed
	WalError  string          `json:"wal_error,omitempty"` // set if logging this op to the WAL failed;
	                                                          // the book operation ITSELF already
	                                                          // committed (Book has no rollback), so
	                                                          // this is reported rather than silently
	                                                          // swallowed -- a caller that cares about
	                                                          // the durability guarantee needs to know
	                                                          // the guarantee just broke for this op
	// Value carries the result of the single-number query ops (mid, spread,
	// last_px, remaining), paired with Present to say whether the book had an
	// answer at all.
	//
	// It deliberately has NO omitempty, for the same reason Position below
	// does not: zero is a legitimate answer for several of these (a remaining
	// quantity of 0 on a fully-filled order, most obviously), and omitempty
	// would drop exactly that case from the JSON, producing a KeyError on the
	// Python side in the one situation the caller most needs to distinguish.
	// That bug already happened once here with Position; not repeating it.
	Value int64 `json:"value"`
	// STPCancels: resting orders cancelled by self-trade prevention. Part of
	// book.Stats since STP was added, but the "stats" op did not copy it into
	// the response, so the one number that says whether STP is doing anything
	// was unreachable from Python.
	STPCancels uint64 `json:"stp_cancels,omitempty"`
	// Position deliberately has NO omitempty: a real position of 0
	// (flat, or an owner who has never traded) is a legitimate, meaningful
	// value, and omitempty would silently drop it from the response --
	// exactly what happened here (caught directly: the Python client's
	// resp["position"] KeyError'd on a flat position, which is the single
	// most common case a caller would check). Every other field in this
	// struct is fine with omitempty because their zero value genuinely
	// means "not applicable to this op" -- Position is the one field
	// where zero is itself the answer.
	Position  int64           `json:"position"`
}

func sideOf(s string) book.Side {
	if s == "sell" {
		return book.Sell
	}
	return book.Buy
}

func sideStr(s book.Side) string {
	if s == book.Sell {
		return "sell"
	}
	return "buy"
}

func typeOf(s string) book.OrdType {
	switch s {
	case "market":
		return book.MarketOrder
	case "stop_limit":
		return book.StopLimitOrder
	default:
		return book.LimitOrder
	}
}

func tifOf(s string) book.TimeInForce {
	switch s {
	case "ioc":
		return book.IOC
	case "fok":
		return book.FOK
	default:
		return book.GTC
	}
}

func main() {
	var bk *book.Book
	var walWriter *wal.Writer
	defer func() {
		if walWriter != nil {
			walWriter.Close()
		}
	}()

	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()

	for in.Scan() {
		line := in.Bytes()
		if len(line) == 0 {
			continue
		}
		var req request
		if err := json.Unmarshal(line, &req); err != nil {
			writeResp(out, response{OK: false, Error: fmt.Sprintf("bad request json: %v", err)})
			continue
		}

		resp := response{ID: req.ID}

		// Every op below except new_book operates on `bk`, and every op that
		// takes a payload dereferences an optional pointer field. Neither is
		// guaranteed by the JSON decoder -- a message with a missing or
		// mistyped field decodes successfully with that field left nil, and
		// dereferencing it panics the whole process rather than rejecting
		// one bad request. That distinction matters a great deal here:
		// nothing about the wire protocol stops a bug in ANY client
		// (Python, or anything else that speaks it) from sending a
		// malformed request, and a server that crashes on bad input is not
		// something a caller can build a reliable simulation loop against.
		// This was not a hypothetical: a real client-side bug (a stale
		// cached order id) sent exactly this -- a "cancel" with no id --
		// and took the whole process down with a nil pointer dereference
		// before these guards existed. Every case below now validates its
		// inputs and returns an error response instead.
		if req.Op != "new_book" && bk == nil {
			resp.Error = "no book: call new_book before any other op"
			writeResp(out, resp)
			continue
		}

		switch req.Op {
		case "new_book":
			if req.Config == nil {
				resp.Error = "new_book: missing config"
				break
			}
			cfg := book.Config{
				MinPx: book.Price(req.Config.MinPx), MaxPx: book.Price(req.Config.MaxPx),
				Tick: book.Price(req.Config.Tick), Capacity: req.Config.Capacity,
				PriceCollarBps: req.Config.PriceCollarBps,
				PositionLimit:  book.Qty(req.Config.PositionLimit),
			}
			nb, err := book.New(cfg)
			if err != nil {
				resp.Error = err.Error()
				break
			}

			if req.Config.WalPath != nil {
				// Recovery, if a prior session's log exists at this path --
				// replay happens BEFORE the writer is opened for new
				// appends, and against the fresh (empty) book only, exactly
				// the precondition wal.Replay documents as required for a
				// correct reconstruction.
				n, rerr := wal.Replay(*req.Config.WalPath, nb)
				if rerr != nil {
					resp.Error = fmt.Sprintf("wal replay failed: %v", rerr)
					break
				}
				resp.Recovered = n

				w, werr := wal.Create(*req.Config.WalPath)
				if werr != nil {
					resp.Error = fmt.Sprintf("wal open for append failed: %v", werr)
					break
				}
				if walWriter != nil {
					walWriter.Close()
				}
				walWriter = w
			} else {
				walWriter = nil
			}

			bk = nb
			resp.OK = true

		case "submit":
			if req.Order == nil {
				resp.Error = "submit: missing order"
				break
			}
			o := book.Order{
				ID: book.OrderID(req.Order.ID), Owner: req.Order.Owner,
				Px: book.Price(req.Order.Px), StopPx: book.Price(req.Order.StopPx),
				Qty: book.Qty(req.Order.Qty), Side: sideOf(req.Order.Side),
				Type: typeOf(req.Order.Type), TIF: tifOf(req.Order.TIF),
			}
			fills, reject := bk.Submit(o)
			resp.OK = true
			resp.Reject = reject.String()
			for _, f := range fills {
				resp.Fills = append(resp.Fills, wireFill{
					Seq: f.Seq, TakerID: uint64(f.TakerID), MakerID: uint64(f.MakerID),
					Px: int64(f.Px), Qty: int64(f.Qty), TakerSide: sideStr(f.TakerSide),
				})
			}
			// Log only ACCEPTED submits, and only AFTER Submit returns --
			// logging a rejected order would replay a no-op on recovery,
			// and logging before the call risks recording an order that
			// then fails validation and never actually entered the book.
			if walWriter != nil && reject == book.RejectNone {
				if werr := walWriter.LogSubmit(o); werr != nil {
					resp.WalError = werr.Error()
				}
			}

		case "cancel":
			if req.Cancel == nil {
				resp.Error = "cancel: missing cancel_id"
				break
			}
			reject := bk.Cancel(book.OrderID(*req.Cancel))
			resp.OK = true
			resp.Reject = reject.String()
			if walWriter != nil && reject == book.RejectNone {
				if werr := walWriter.LogCancel(book.OrderID(*req.Cancel)); werr != nil {
					resp.WalError = werr.Error()
				}
			}

		case "best_bid":
			px, qty, ok := bk.BestBid()
			resp.OK, resp.Px, resp.Qty, resp.Present = true, int64(px), int64(qty), ok

		case "best_ask":
			px, qty, ok := bk.BestAsk()
			resp.OK, resp.Px, resp.Qty, resp.Present = true, int64(px), int64(qty), ok

		case "depth":
			n := 10
			if req.Depth != nil {
				n = *req.Depth
			}
			bids, asks := bk.Depth(n)
			resp.OK = true
			for _, l := range bids {
				resp.Bids = append(resp.Bids, wirePriceLevel{int64(l.Px), int64(l.Qty), l.Count})
			}
			for _, l := range asks {
				resp.Asks = append(resp.Asks, wirePriceLevel{int64(l.Px), int64(l.Qty), l.Count})
			}

		// mid, spread and last_px were all reachable on book.Book but not over
		// the wire, so sim/bourse_sim/engine.py reconstructed mid() itself
		// from best_bid/best_ask. That reimplementation is a second definition
		// of the same quantity, free to drift from the engine's -- and the
		// simulation's most consequential bug to date (KNOWN_ISSUES #3) was a
		// mid-price fallback behaving differently than intended.
		case "mid":
			px, ok := bk.Mid()
			resp.OK, resp.Value, resp.Present = true, int64(px), ok

		case "spread":
			sp, ok := bk.Spread()
			resp.OK, resp.Value, resp.Present = true, int64(sp), ok

		case "last_px":
			px, ok := bk.LastPx()
			resp.OK, resp.Value, resp.Present = true, int64(px), ok

		// remaining answers "is this order still resting, and for how much?"
		// Without it a caller tracking a resting GTC order can only infer that
		// from its own fill bookkeeping, which is precisely the kind of
		// duplicated state the ledger design elsewhere avoids.
		case "remaining":
			if req.OrderID == nil {
				resp.Error = "remaining: missing order_id"
				break
			}
			qty, ok := bk.Remaining(book.OrderID(*req.OrderID))
			resp.OK, resp.Value, resp.Present = true, int64(qty), ok

		case "position":
			if req.Owner == nil {
				resp.Error = "position: missing owner"
				break
			}
			resp.OK = true
			resp.Position = bk.Position(*req.Owner)

		case "stats":
			st := bk.Stats()
			resp.OK = true
			resp.Trades, resp.Volume, resp.Live, resp.Sequence = st.Trades, int64(st.Volume), st.LiveOrders, st.Sequence
			resp.STPCancels = st.STPCancels

		case "check":
			if err := bk.Check(); err != nil {
				resp.Error = err.Error()
			} else {
				resp.OK = true
			}

		default:
			resp.Error = fmt.Sprintf("unknown op %q", req.Op)
		}

		writeResp(out, resp)
	}
	if err := in.Err(); err != nil {
		fmt.Fprintln(os.Stderr, "scan error:", err)
		os.Exit(1)
	}
}

func writeResp(out *bufio.Writer, r response) {
	b, err := json.Marshal(r)
	if err != nil {
		fmt.Fprintln(os.Stderr, "marshal error:", err)
		return
	}
	out.Write(b)
	out.WriteByte('\n')
	out.Flush() // flush per-response: this is a request/response protocol,
	            // not a stream, so the Python side must see each reply
	            // immediately rather than waiting on a buffer to fill.
}
