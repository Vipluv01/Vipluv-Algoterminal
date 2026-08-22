# Known issues — market simulation

Honest status of the agent-based simulation as of the last investigation
session, written up because the investigation itself is worth more than
hiding an open problem behind a clean-looking final number.

## Resolved during this investigation

Four real bugs were found and fixed by not trusting the first number a
change produced:

1. **Seed-liquidity anchor.** The book was seeded with 1000-lot orders to
   avoid `mid()` returning `None` on step 0. Whenever a maker's spread
   temporarily widened past that seed's price band, the seed became a
   permanent liquidity wall the simulated market couldn't trade through.
   Fixed: sized down to 30 lots, comparable to real order flow, so it gets
   consumed within the first few trades.

2. **Unstable `k` estimation** in the Avellaneda-Stoikov maker. The
   order-arrival decay parameter was refit from scratch every step from a
   thin, noisy book, causing it to swing between a floor value and an
   arbitrary default and destabilizing the maker's own quoting. Fixed: hold
   the previous estimate when the fit isn't well-supported by the data,
   rather than snapping to a hardcoded fallback.

3. **Stale-mid-reverts-to-seed.** The most consequential bug. Whenever
   `eng.mid()` returned `None` (book momentarily one-sided — see below,
   this is common), the code fell back to the *original* seed price rather
   than the *last observed* mid. This silently snapped the recorded price
   series back to the fixed starting value on a majority of steps,
   fabricating large synthetic jumps that had nothing to do with market
   dynamics and were feeding directly into the stylized-facts kurtosis
   measurement. Fixed: track the last observed mid persistently and use
   that as the fallback everywhere.

4. **Server crash on malformed input.** While stress-testing larger quote
   sizes, the Go `simserver` process crashed outright — a nil pointer
   dereference on a `cancel` request with no id. Root cause: a client-side
   state bug (a maker's cached quote price could go stale independently of
   its order id after hitting an inventory limit), *and* the server had no
   defense against it — any malformed request could take the whole process
   down. Fixed both: synced the cached id/price state on the Python side,
   and hardened the Go server to validate every request field and return an
   error response instead of panicking, regardless of what a client sends.
   Regression-tested directly (`TestMalformedRequestsDoNotCrashTheServer`).

## Open: volatility clustering shows the wrong sign

Of the three stylized facts checked, two hold robustly across every
configuration tested:

- **Fat tails**: excess kurtosis 2–4, consistently positive and significant.
- **Weak return autocorrelation**: consistently near zero, as real markets
  show (no easy lag-1 arbitrage).

The third does not: **|return| autocorrelation is consistently negative**
(around -0.34 to -0.44 depending on configuration), where real markets show
a **positive** value (big moves cluster with big moves). This means the
simulation currently produces an *alternating* big-move/small-move pattern
rather than genuine volatility clustering.

**Root-cause investigation, in order:**

- Ruled out: book "staleness" from the maker's cancel-then-post cycle
  leaving a gap. Measured directly (97% of steps showed a one-sided book);
  fixed by reordering to post-before-cancel; **staleness was unchanged**
  (still 97%) and the sign didn't improve. The real cause of staleness turned
  out to be simpler: a single maker's fixed-size quote is often smaller than
  one aggressive order and gets fully consumed before the next refresh —
  that's real thin-liquidity behavior, not a bug.
- Partially supported, not conclusively: quote size. An isolated test
  (bypassing the full `simulate.py` pipeline) showed *positive*, significant
  clustering at `quote_size=500`. Re-run through the actual project pipeline
  (`simulate.run_simulation`) at a comparable size, the sign was **still
  negative** and P&L got sharply worse (large quotes against informed flow
  compounds adverse-selection losses). The two code paths are not currently
  equivalent and that discrepancy itself hasn't been root-caused.

**Ruled out: inventory-skew overcorrection.** Tested directly via a proper
sweep of `inventory_skew_ticks_per_unit` (0.0, 0.005, 0.02, 0.05) run
through the actual `run_simulation` pipeline (see "methodology fix" below).
At `skew=0.0` — no inventory-based quote adjustment at all — volatility
clustering was still strongly negative (-0.29). Since no overcorrection
mechanism is possible when skew is disabled entirely, this hypothesis is
definitively eliminated, not just deprioritized.

**Current best-supported hypothesis:** the recorded mid is largely *the
market maker's own quote midpoint*, not an independent price signal. One
maker sets almost the entire two-sided market and fully re-centers its
quotes on the last observed touch every single refresh (with or without
inventory skew). A trade that moves the touch up causes the very next quote
to re-center there; subsequent order flow — mostly undirected noise trading
— has roughly equal odds of hitting either the new bid or the new ask, and
either outcome pulls the touch back toward where it recently was. That is
structurally the same phenomenon as bid-ask bounce (Roll 1984), except
manifesting at the level of the maker's own re-centered mid rather than
between two independent standing quotes. It requires only one dominant
continuous quoter to produce, which is exactly this simulation's setup.
Real markets avoid it by having many independent liquidity providers at
staggered price levels, so no single participant's re-quote decision
dominates the touch.

**Multi-maker: properly implemented and tested — result is real, and it's a
mixed one, not a fix.** `run_simulation` now accepts a list of makers (not
just one), with correct per-maker fill attribution including inter-maker
trades (a real if/elif bug was caught and fixed in the process — see
`test_route_fills_updates_both_sides_of_an_inter_maker_trade`). Swept
n=1/2/3/5 makers with staggered spreads through the real, validated
pipeline:

| n_makers | kurtosis | |return\| autocorr | significant? |
|---|---|---|---|
| 1 | 2.56 | -0.40 | yes (wrong sign) |
| 2 | 198.32 | -0.02 | **no** — noise |
| 3 | 965.68 | -0.002 | **no** — noise |
| 5 | 64.92 | -0.07 | yes (wrong sign, smaller) |

Honest reading: more makers moves the wrong-signed autocorrelation toward
zero (n=2, n=3 are statistically indistinguishable from no clustering at
all, which is progress over confidently-wrong) but never flips it positive,
and n=5 is still significantly negative. Worse, it introduces a NEW
artifact — kurtosis explodes to 65–966, versus the single-maker baseline's
realistic 2.56 — plausibly from makers periodically crossing each other's
staggered spread levels and producing occasional large jumps as different
quote layers get "discovered" by aggressive flow. Not investigated further.

**Net assessment:** multi-maker is not the fix. It trades one artifact
(confidently wrong-signed clustering) for a different one (kurtosis
explosion) depending on n. The underlying mechanism is still not fully
understood. This is now a properly closed investigation with a real,
reproducible negative result — not an open guess.

**What NOT to do next:** keep raising quote size or tuning inventory skew.
Both were tested properly (quote size to 400, skew from 0 to 0.05) and
neither fixes the sign; quote size additionally made realized P&L an order
of magnitude worse (heuristic maker: -$13.5K at qty=50 vs -$431K at
qty=400) without resolving anything.

**Methodology fix made along the way:** `run_simulation` initially always
constructed its own `MarketMaker` internally, so testing a parameter sweep
meant hand-copying the simulation loop into a separate script — which
diverged from the real pipeline (the quote-size result initially looked
positive in that copy, but negative when re-run through the real code).
`run_simulation` now accepts an optional `maker=` argument, so every sweep
in this document was run through the exact same code path the project's
real results come from.
