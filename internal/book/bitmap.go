package book

import "math/bits"

// bitmap is a three-level hierarchical bitmap over price ticks, used to find
// the best bid or ask without walking empty price levels.
//
// A naive book scans outward from the last known best price to find the next
// one. That is fine until someone sweeps a wide, sparse book, at which point
// the scan is unbounded and the tail latency shows it. Here each level
// summarises the one below it -- a set bit at level N means "there is at least
// one set bit in the corresponding word at level N-1" -- so min and max are
// three word reads and three CPU bit-scan instructions regardless of how
// sparse or wide the book is.
//
// Three levels cover 64^3 = 262,144 ticks, which is far more than any single
// instrument needs.
type bitmap struct {
	l0 []uint64 // one bit per tick
	l1 []uint64 // one bit per l0 word
	l2 []uint64 // one bit per l1 word
}

func newBitmap(nTicks int) *bitmap {
	w0 := (nTicks + 63) >> 6
	w1 := (w0 + 63) >> 6
	w2 := (w1 + 63) >> 6
	return &bitmap{
		l0: make([]uint64, w0),
		l1: make([]uint64, w1),
		l2: make([]uint64, w2),
	}
}

func (b *bitmap) set(i int) {
	w := i >> 6
	b.l0[w] |= 1 << uint(i&63)
	j := w >> 6
	b.l1[j] |= 1 << uint(w&63)
	b.l2[j>>6] |= 1 << uint(j&63)
}

// clear unsets tick i, and prunes the summary levels only when the word below
// them has emptied out. The early returns matter: most clears touch one word.
func (b *bitmap) clear(i int) {
	w := i >> 6
	b.l0[w] &^= 1 << uint(i&63)
	if b.l0[w] != 0 {
		return
	}
	j := w >> 6
	b.l1[j] &^= 1 << uint(w&63)
	if b.l1[j] != 0 {
		return
	}
	b.l2[j>>6] &^= 1 << uint(j&63)
}

func (b *bitmap) test(i int) bool {
	return b.l0[i>>6]&(1<<uint(i&63)) != 0
}

func (b *bitmap) empty() bool {
	for _, w := range b.l2 {
		if w != 0 {
			return false
		}
	}
	return true
}

// min returns the lowest set tick, or -1 if empty. This is the best ask.
func (b *bitmap) min() int {
	for k, w := range b.l2 {
		if w == 0 {
			continue
		}
		j := k<<6 + bits.TrailingZeros64(w)
		i := j<<6 + bits.TrailingZeros64(b.l1[j])
		return i<<6 + bits.TrailingZeros64(b.l0[i])
	}
	return -1
}

// max returns the highest set tick, or -1 if empty. This is the best bid.
func (b *bitmap) max() int {
	for k := len(b.l2) - 1; k >= 0; k-- {
		w := b.l2[k]
		if w == 0 {
			continue
		}
		j := k<<6 + 63 - bits.LeadingZeros64(w)
		i := j<<6 + 63 - bits.LeadingZeros64(b.l1[j])
		return i<<6 + 63 - bits.LeadingZeros64(b.l0[i])
	}
	return -1
}

// nextAbove returns the lowest set tick strictly greater than i, or -1.
// Used for depth walks and fill-or-kill sizing, not the hot path, so a plain
// word scan is the right tradeoff against more index machinery.
func (b *bitmap) nextAbove(i int) int {
	i++
	if i < 0 {
		i = 0
	}
	w := i >> 6
	if w >= len(b.l0) {
		return -1
	}
	if m := b.l0[w] &^ ((1 << uint(i&63)) - 1); m != 0 {
		return w<<6 + bits.TrailingZeros64(m)
	}
	for w++; w < len(b.l0); w++ {
		if b.l0[w] != 0 {
			return w<<6 + bits.TrailingZeros64(b.l0[w])
		}
	}
	return -1
}

// nextBelow returns the highest set tick strictly less than i, or -1.
func (b *bitmap) nextBelow(i int) int {
	i--
	if i < 0 {
		return -1
	}
	w := i >> 6
	if w >= len(b.l0) {
		w = len(b.l0) - 1
		i = w<<6 + 63
	}
	var mask uint64
	if i&63 == 63 {
		mask = ^uint64(0)
	} else {
		mask = (1 << uint((i&63)+1)) - 1
	}
	if m := b.l0[w] & mask; m != 0 {
		return w<<6 + 63 - bits.LeadingZeros64(m)
	}
	for w--; w >= 0; w-- {
		if b.l0[w] != 0 {
			return w<<6 + 63 - bits.LeadingZeros64(b.l0[w])
		}
	}
	return -1
}
