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

## Related: realized volatility is near-zero, and appears to be the SAME defect

`webapp/app/markets.py`'s `SymbolMarket` (a separate agent population built
from these same `NoiseTrader`/`InformedTrader`/`MarketMaker` classes, driving
`webapp/app/backtest/paths.py`'s `generate_market_paths`) was found during
Phase 6 verification to produce annualized realized volatility of roughly
2-11% depending on symbol — real NSE equities run 15-35%. Investigated
directly (not re-litigating the clustering-sign hypotheses above; this is a
different metric, magnitude rather than sign, checked against a codepath
those sweeps never measured it on). Correcting for a real measurement bug
first (an earlier report of "0.01-0.07%" was `_sharpe_ratio`'s own
`sqrt(TRADING_DAYS_PER_YEAR)` mistake applied to raw vol — a bar here is one
`registry.step_all()`, i.e. one simulated SECOND, exactly matching
`FundamentalProcess`'s own `dt = 1/(252*6.5*3600)`, not one trading day; see
`webapp/app/backtest/engine.py`'s `BARS_PER_YEAR`), the honest per-bar figure
annualizes to 2-11%, not 0.01-0.07% — a real gap from the 15-35% target, but
a much smaller one than first measured.

**Eleven parameters/mechanisms tested directly against `SymbolMarket`
(TCS, seed 42 unless noted, 1000-2000 steps), none of which move realized
vol meaningfully:**

| Lever | Range tested | Effect |
|---|---|---|
| `InformedTrader.threshold_ticks` | 2.0 -> 0.25 | none (bit-identical output) |
| `InformedTrader.signal_noise_sigma` | 0.01 -> 0.0001 | none (flat until the very last point, then a small, non-monotonic bump) |
| `FundamentalProcess.sigma` | 0.2 -> 200 (1000x) | **none** — 2.07% at sigma=0.2, 1.71% at sigma=200 |
| `MarketMaker.quote_size` | 100 -> 5 | none |
| Maker refresh cadence | every step -> every 50 steps | none (slight decrease) |
| `NoiseTrader.aggressive_prob` | 0.2 -> 0.8 | none (0.8 actually lower); **1.0 collapses to exactly 0%** (see below) |
| Reservation-price EMA smoothing (new: blend the maker's quote toward a slow-moving average instead of the raw last touch, to test whether giving the market "memory" unlocks trend accumulation) | alpha 1.0 -> 0.005 | none |
| `MarketMaker.inventory_skew_ticks_per_unit` | 0.02 -> 0.0 | none — but uninformative: maker inventory returns to ~0 every run, so skew's own coefficient is multiplied by ~0 regardless |
| `MarketMaker.base_half_spread_ticks` | 3.0 -> 0.5 | **negative** — narrowing the spread makes it WORSE (1.43% at 2.0, exactly 0.00% at <=1.0: a spread this tight always has a resting order near enough to absorb flow without the touch ever moving) |
| A combined change (all of the above simultaneously: smaller quote, tighter threshold, more/faster informed and noise traders) | — | still ~2.08%, no improvement over baseline |
| Random seed | 20 independent seeds, TCS | 2.00%-2.21% — a strikingly narrow range for supposedly-independent draws |

**Per-bar vol is stable across window length** (500 to 8000 steps: 8.45e-6 to
8.14e-6, no meaningful decay) — this behaves like a genuine, if too-small,
near-i.i.d. random walk, not a bounded/mean-reverting process whose measured
variance would shrink as the window grows. That rules out "it's secretly
mean-reverting so nothing accumulates" as the mechanism.

**What actually determines the ~2-11% figure, found by measuring all 7
production symbols (5 seeds each, 1500 steps):**

| symbol | s0 | tick/s0 | measured ann. vol |
|---|---|---|---|
| RELIANCE | 2900 | 0.0017% | 2.9% |
| TCS | 4150 | 0.0012% | 2.1% |
| HDFCBANK | 1650 | 0.0030% | 5.2% |
| INFY | 1850 | 0.0027% | 4.6% |
| ICICIBANK | 1250 | 0.0040% | 6.8% |
| TATAMOTORS | 980 | 0.0051% | 8.7% |
| SBIN | 810 | 0.0062% | 10.4% |

`annualized_vol_pct / (tick_size/s0 * 100)` is 1679-1729 for every single
symbol — a strikingly tight, essentially linear relationship. **Realized
volatility here is overwhelmingly a function of the tick grid's coarseness
relative to price, not of any economic parameter** (fundamental drift,
informed signal, noise flow, maker behavior). Combined with the raw tick
series showing every observed move is exactly +/-1 or +/-2 ticks, never
more — regardless of how extreme the fundamental's own sigma is — the
picture is: informed/noise flow occasionally crosses and moves the touch by
one tick with some roughly-fixed probability per step, and that a move is
essentially always exactly one or two ticks in size is what caps realized
variance, independent of the ECONOMIC size of whatever signal caused it.

This is a magnitude-focused extension of the SAME root cause already
identified above (the maker re-centers to the raw last touch with no
persistent reference, so nothing about the fundamental's true movement
survives into a multi-tick, sustained price change) — new evidence, not a
re-litigation: none of the above eleven experiments duplicate a
configuration already tested in the sign investigation, and several
(EMA-smoothed reservation price, the combined multi-parameter sweep, the
tick/s0 relationship, the 20-seed spread) are genuinely new probes this
document didn't previously report.

**Net assessment, same honesty standard as the section above:** this is not
fixed. A real, principled, hour-scale investigation (eleven independent
experiments, several combined) found no parameter-level lever that closes
the gap to a real-NSE 15-35% band without an architectural change to how the
maker's reservation price is formed — the same class of fix multi-maker
already attempted for the sign problem and which traded one artifact for
another. `webapp/app/backtest/paths.py`'s realized-vol regression test
(`test_realized_vol_lands_in_a_defensible_band`) therefore asserts against
the REAL, MEASURED, per-symbol range this architecture currently and
reliably produces (with margin for seed variation), not the aspirational
15-35% NSE figure — an honest, reproducible band beats a fabricated one, and
a guard that can't pass against reality isn't a guard.

**What NOT to try next, now ruled out by direct measurement:** `fundamental_
sigma` (tested to 1000x normal with zero effect), `signal_noise_sigma`,
`threshold_ticks`, `quote_size` (already partly covered above for the sign
issue, now also for magnitude), reservation-price smoothing, refresh cadence,
inventory skew (though this one's test was structurally uninformative, not
a clean negative). **A structural change to how the maker forms its
reservation price** (not just adding more makers, which is already a ruled-
out variant of this) is the most promising untried direction, but is a
deeper investigation than fits in one pass — flagged here, not attempted.

## Related: synthetic options carry no real gamma risk

A downstream consequence of the same near-zero realized volatility, found
while reviewing the `delta_neutral` options backtest strategy (`webapp/app/
strategies/delta_neutral.py`, backtested via `webapp/app/backtest/adapters.
OptionsBacktestAdapter`). Decomposing that strategy's per-bar option P&L
into its delta and gamma components (`P&L ~= delta*dS + 0.5*gamma*dS^2 +
theta*dt`) over a 500-bar path:

- `sum(delta * dS) ~= 0.90`
- `sum(0.5 * gamma * dS^2) ~= 0.0011`

Gamma P&L is ~800x smaller than delta P&L. In other words: a delta-hedged
short option position in this simulation collects theta decay with
essentially no offsetting convexity risk ever materializing, because the
underlying moves far too little (see the section above) for gamma to matter
at any strike off the exact spot. This is why an earlier, uncalibrated
version of the options pricing model made short-premium strategies
(`short_strangle`, `iron_condor`) look like a risk-free win — that specific
bug (option pricing using a fixed 18% IV disconnected from what the
underlying actually does) was fixed by calibrating implied vol to the
underlying's own realized volatility (`app/options/chain.py`'s
`realized_vol_annualized`), which correctly collapsed those strategies'
Sharpe to "no evidence of edge" rather than a fabricated win. But the DEEPER
limitation documented here survives that fix: even with option pricing
correctly calibrated to this simulation's own (too-low) realized vol, the
simulation still doesn't move enough for a real desk's actual risk — being
short gamma against a large adverse move — to ever show up in a backtest.
Every options number in this project should be read with that caveat: the
guard (`app/backtest/engine.py`'s `MAX_PLAUSIBLE_ANNUALIZED_RATIO`) prevents
an impossible number from being reported, but it does not, and cannot,
manufacture the missing risk itself.

**Not attempted in this pass, by design** — this is a consequence of the
volatility-generation limitation above, not a separate bug in the options
code, and fixing the root cause (this document's own open, actively-
investigated finding) is out of scope for a documentation-only pass. A
known limitation stated with real numbers is worth more here than a fix
attempted under time pressure and shipped half-verified.

## Untested hypothesis: tick quantization may be the shared root cause of both open findings

Raised during cross-session review of the realized-volatility investigation
above (not yet tested against `SymbolMarket` -- recorded here specifically
so it isn't lost, per this project's own discipline of writing up an
investigation rather than losing it to a chat transcript).

The `tick_size/price ~= 1700x` relationship this document already measured,
combined with `fundamental_sigma` having ZERO effect on realized vol even
at 1000x normal, suggests price formation here is dominated by the tick
grid's coarseness, not by the informed-trading channel actually carrying
information into the book. If most price changes are simple single-tick
bid-ask bounce on a fixed grid, that mechanically produces alternating-sign
returns -- which would show up as NEGATIVE autocorrelation in `|return|`,
exactly the wrong-signed clustering this document's own open section
describes. Both open findings (near-zero realized vol, wrong-signed
clustering) may be one root cause, not two.

**Proposed, testable experiment** (not yet run): sweep tick_size (or,
equivalently, hold tick_size fixed and vary s0) and measure BOTH realized
vol and `|return|` autocorrelation at each point, through the real
`run_simulation`/`SymbolMarket` pipeline (not a hand-copied loop -- see this
document's own "methodology fix" note above on why that distinction
mattered before). **Prediction:** as the grid gets finer relative to price,
realized vol should rise (already measured, see above) AND `|return|`
autocorrelation should move from negative toward zero or positive. If it
does, the clustering defect is bid-ask bounce driven by tick coarseness,
and the fix is a finer effective grid (or a different rounding/quantization
approach), not a further change to the economic/agent layer -- which this
document's own extensive prior investigation (staleness, inventory skew,
quote size, n_makers) has already spent considerable effort ruling out
without touching tick granularity itself. If it doesn't, this hypothesis is
eliminated cleanly, the same way inventory-skew overcorrection was.

**Not run in this pass.** Recorded as a concrete, falsifiable next
direction rather than chased under time pressure.

## Related but DISTINCT: most equity strategy backtests are fee-dominated, not volatility-limited

Found while investigating why `webapp/results/backtests.json` renders a valid
Sharpe for only 1 of 12 registered strategies after the `BARS_PER_YEAR`
annualization fix (`webapp/app/backtest/engine.py`, see that file's own
`MAX_PLAUSIBLE_ANNUALIZED_RATIO` comment). Initially suspected to be the same
family of defect as this document's other findings above -- it is not, and
the two should not be conflated.

**What was ruled out first.** Block-aggregating equity-curve returns into
coarser periods (1 second up to 50 minutes) before computing Sharpe, to test
whether autocorrelated microstructure noise (bid-ask bounce, per this
document's own open finding above) was inflating the raw per-bar variance:
the annualized ratio held flat (-426 to -314) across three orders of
magnitude of block size for `alpha_rsi_ema`. Under genuinely autocorrelated
noise, aggregating should have pulled the ratio toward something plausible
as the noise averaged out. It didn't -- ruling out aggregation-frequency and
microstructure noise as the cause.

**What was ruled out second.** Whether a longer backtest horizon (same 1-
second bar granularity, more real trades and more accumulated price
movement) resolves it. It does not -- see the measured numbers below.

**The actual, measured cause: turnover x transaction cost exceeds
per-trade edge.** Single path, `alpha_rsi_ema`, `ICICIBANK`, 500 bars,
seed 42, `fee_bps=2.0`:

| | value |
|---|---|
| orders | 24 |
| total notional | Rs 300,125.50 |
| total fee cost | Rs 60.03 |
| realized P&L | Rs -65.53 |
| fee as % of \|P&L\| | **91.6%** |

If thin realized volatility (this document's own finding above) were the
binding constraint, fee share would FALL as the horizon grows -- more time
means more accumulated price movement relative to a roughly fixed per-trade
cost. It does not:

| n_bars | orders | fee cost | P&L | fee as % of \|P&L\| |
|---|---|---|---|---|
| 500 | 24 | Rs 60.03 | -Rs 65.53 | 91.6% |
| 5,000 | 236 | Rs 588.60 | -Rs 572.10 | 102.9% |
| 20,000 | 848 | Rs 2,108.70 | -Rs 2,068.20 | 102.0% |

Fee share stays flat-to-over 100% regardless of horizon. That rules out the
volatility explanation directly and points at the real cause: this
strategy's round-trip frequency, multiplied by the fee cost per round trip,
exceeds whatever directional edge it has per trade. **The readings above
100% are the sharpest part of this finding**: they mean gross directional
P&L is sometimes NET POSITIVE at longer horizons (the strategy has real
edge) while net P&L (after fees) is still negative -- the edge exists, it is
just smaller than the transaction cost extracting it. At 5,000 bars the
gross edge covers roughly 3% of the fee bill.

`MAX_PLAUSIBLE_ANNUALIZED_RATIO` (`webapp/app/backtest/engine.py`) is
correctly rejecting these results as near-deterministic -- it is not
miscalibrated, and this is not a bug in the guard. `fee_bps` must not be
tuned down to make these numbers clear the guard (an explicit instruction on
this project, restated here so it isn't forgotten): a smaller fee doesn't
fix a strategy with insufficient edge, it just hides the same problem.

**Arithmetic note, so nobody re-derives this later:** position sizing cannot
fix this. Fee cost is proportional to traded notional, and gross P&L is
(approximately) proportional to traded notional too, so the ratio between
them is size-invariant. The only two real levers are trading less often
(lower turnover) or having more edge per trade -- neither of which this
investigation attempted, since both are strategy-design changes outside a
documentation-only pass.

**Open follow-up, deliberately not attempted here:** the four options
strategies (`iron_condor`, `calendar_spread`, `short_strangle`,
`delta_neutral`) cannot be backtested at ANY feasible horizon today -- their
real `hold_bars` (117,000-468,000, recalibrated to genuine multi-day holds
under the corrected 1-bar-=-1-second clock) cannot fit inside a sweep sized
for the equity strategies above, so they skip outright rather than produce a
fabricated result. Making a long-enough sweep tractable is itself blocked on
a separate, independent finding: `pairs_cointegration.py`/`pairs_kelly.py`'s
per-bar cointegration test re-runs on the full growing price history every
bar, measured at 5.5ms -> 76.2ms -> 499ms as history grows from 300 to
14,400 bars (superlinear, roughly O(n^1.1-1.35) per bar, so total sweep cost
is worse than quadratic) -- a trailing window would fix both the cost and a
real statistical inconsistency with `KalmanBetaAlpha`'s time-varying-beta
premise (see the inline comment at `pairs_cointegration.py`'s
`evaluate_pair`, marked NOT YET DONE). These two items -- the options
strategies' horizon and the cointegration cost that blocks reaching it --
are one open pair, not attempted in this pass, and deliberately not bundled
into this fee-drag finding, which is fully resolved on its own.
