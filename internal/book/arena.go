package book

// handle addresses an order slot in the arena. It is deliberately an integer
// rather than a *node: the Go garbage collector must scan every pointer it can
// reach on every cycle, so a book holding a million pointer-linked orders makes
// the GC walk a million-node graph and the p99.9 latency reflects that. A slice
// of pointer-free structs is a single allocation the collector never traverses.
type handle uint32

const nilHandle handle = ^handle(0)

// node is the in-arena representation of a live order. It contains no pointers
// and no strings, which is what keeps the arena opaque to the collector.
type node struct {
	id    OrderID
	px    Price
	qty   Qty // remaining, always > 0 while linked
	seq   uint64
	prev  handle
	next  handle
	tick  int32 // price level index, or stop-trigger index while pending
	owner uint32
	side  Side
	typ   OrdType
	tif   TimeInForce
	// pending marks a stop order waiting for its trigger. Such orders are
	// indexed by trigger price in the stop books, not by limit price in the
	// visible book, so every lookup must branch on this.
	pending bool
}

// arena is a fixed-capacity slab allocator with an intrusive free list. Slots
// are recycled through node.next while free, so the free list costs no extra
// memory. Allocation and release are both a handful of instructions with no
// syscall and no GC involvement, which is what keeps the hot path flat.
type arena struct {
	nodes []node
	free  handle
	used  int
}

func newArena(capacity int) *arena {
	a := &arena{nodes: make([]node, capacity)}
	for i := 0; i < capacity-1; i++ {
		a.nodes[i].next = handle(i + 1)
	}
	a.nodes[capacity-1].next = nilHandle
	a.free = 0
	return a
}

func (a *arena) alloc() handle {
	h := a.free
	if h == nilHandle {
		return nilHandle
	}
	a.free = a.nodes[h].next
	a.used++
	return h
}

func (a *arena) release(h handle) {
	a.nodes[h] = node{next: a.free}
	a.free = h
	a.used--
}

func (a *arena) at(h handle) *node { return &a.nodes[h] }

// Live reports how many orders are currently resting or pending.
func (a *arena) live() int { return a.used }
