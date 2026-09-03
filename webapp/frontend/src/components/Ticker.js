import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { fmtMoney } from "../format.js";
import { DEFAULT_STALE_THRESHOLD_MS, useNow } from "../clock.js";
import { useMode } from "../mode.js";

// A live scrolling ticker strip across every named instrument. In paper/
// virtual mode this fans subscribeMarket() out across all 7 symbols at
// once -- free and unlimited against the simulated engine, the same
// primitive Terminal.js/Charts.js already use per-symbol (never live, so
// no need to route through subscribeMarketForMode here).
//
// In LIVE mode this deliberately does NOT do the same fan-out: N held-
// open real Angel One WebSocket connections for a purely decorative
// strip is exactly what produced a 4+ hour, 1,539-reconnect-attempt
// incident against a real account on 2026-08-28 (see api.js's
// subscribeLiveMarket docstring for the fix on the WS side; this is the
// other half -- not needing a live WS here AT ALL for something that's
// read at a glance, not traded off of). Live mode instead polls GET
// /live/market/history (one short-lived REST request per symbol, not a
// held-open session) on a slow, independent interval.
const LIVE_TICKER_POLL_MS = 30_000;
// Proportional to the poll interval, not DEFAULT_STALE_THRESHOLD_MS
// (3s) -- that threshold assumes a live WS ticking every second; applied
// unchanged to a 30s poll, every live-mode item would read "stale"
// permanently, which is worse than useless as a signal. Two missed polls
// of slack before flagging it.
const LIVE_TICKER_STALE_THRESHOLD_MS = LIVE_TICKER_POLL_MS * 3;

export function Ticker() {
  const [symbols, setSymbols] = React.useState([]);
  const [ticks, setTicks] = React.useState({});
  // LIVE mode only -- the real previous close each live symbol's %-change
  // is computed against (Quote.close, from Angel One's own real quote).
  // Real bug this fixes (confirmed live, 2026-09-03): %-change used to be
  // computed against s.reference_price (NAMED_INSTRUMENTS' static
  // simulated seed price, e.g. ICICIBANK=1250) even in live mode, where
  // `price` is a REAL Angel One number -- comparing a real live price
  // against an unrelated simulated constant produced swings like -55%/
  // +44% for perfectly ordinary real moves. See api.live.quotes' own
  // comment and app/routers/live_market.py's get_live_quotes docstring.
  const [liveCloses, setLiveCloses] = React.useState({});
  // Per-SYMBOL last-tick timestamp, not one shared flag: this component
  // holds N independent WebSocket subscriptions (one per instrument, via
  // the fan-out below), unlike Terminal.js/StatusBar.js which each watch
  // a single connection. Reusing a single boolean here would have to pick
  // one subscription to represent all of them, silently hiding it if a
  // DIFFERENT symbol's feed is the one that actually dropped. Every
  // ticker cell judges its own staleness against its own last tick.
  const [lastUpdatedAt, setLastUpdatedAt] = React.useState({});
  const now = useNow();
  const mode = useMode();

  React.useEffect(() => { api.symbols().then(setSymbols); }, []);

  React.useEffect(() => {
    if (!symbols.length) return;
    // /symbols also returns derived indices (NIFTY50, BANKNIFTY --
    // is_derived: true). Neither data source below has a live per-tick
    // feed for a derived index itself (it's computed FROM the named
    // constituents, not streamed directly) -- subscribing/polling one
    // anyway isn't a graceful no-op on the WS side (market_ws.py rejects
    // it at the handshake, a real visible failure every page load), so
    // it's filtered out here either way; a derived index's price/pct just
    // render as the dash sentinel below rather than a number neither path
    // was ever going to feed.
    const namedSymbols = symbols.filter((s) => !s.is_derived);

    if (mode === "live") {
      // Filtered to live_tradable ONLY in live mode -- confirmed live,
      // 2026-09-03: without this, a symbol with zero real Angel One
      // listings (TATAMOTORS) was being polled here every
      // LIVE_TICKER_POLL_MS right alongside every other real symbol,
      // forever, each attempt a real, guaranteed-to-fail Angel One call
      // that still costs a full round trip through the SAME single-
      // concurrency slot (_call_semaphore) every other live call --
      // including options order confirmation -- has to queue behind.
      // Six-plus hours of this in the real logs lines up exactly with a
      // "everything in live mode feels slow" report. This was previously
      // a deliberate exception (a read-only strip "doesn't need" the
      // filter Terminal.js/ManualTrade.js apply to their pickers) --  but
      // that reasoning assumed an unfiltered poll was harmless, which the
      // real account traffic shows it isn't.
      const liveNamedSymbols = namedSymbols.filter((s) => s.live_tradable);
      let cancelled = false;
      const poll = () => {
        if (!liveNamedSymbols.length) return;
        // ONE batched request for every live symbol, not one per symbol
        // -- also gives real LTP + real previous close together, so this
        // replaces the old per-symbol GET /live/market/history poll
        // entirely rather than adding a second request alongside it.
        api.live.quotes(liveNamedSymbols.map((s) => s.symbol))
          .then((res) => {
            if (cancelled) return;
            const now = Date.now();
            res.quotes.forEach((q) => {
              if (q.ltp == null) return; // unresolvable or genuinely unquoted right now -- leave prior state as-is
              setTicks((prev) => ({ ...prev, [q.symbol]: q.ltp }));
              setLastUpdatedAt((prev) => ({ ...prev, [q.symbol]: now }));
              if (q.close != null) setLiveCloses((prev) => ({ ...prev, [q.symbol]: q.close }));
            });
          })
          .catch(() => {}); // e.g. no broker credential yet -- leave the strip showing whatever it last had
      };
      poll();
      const id = setInterval(poll, LIVE_TICKER_POLL_MS);
      return () => { cancelled = true; clearInterval(id); };
    }

    const unsubs = namedSymbols.map((s) =>
      subscribeMarket(s.symbol, (tick) => {
        setTicks((prev) => ({ ...prev, [s.symbol]: tick.price }));
        setLastUpdatedAt((prev) => ({ ...prev, [s.symbol]: Date.now() }));
      })
    );
    return () => unsubs.forEach((u) => u());
  }, [symbols, mode]);

  if (!symbols.length) return null;

  // Live mode: don't DISPLAY a symbol this strip can never have real
  // data for either -- a derived index (NIFTY50/BANKNIFTY, no live
  // quote source at all) or a non-live_tradable name (TATAMOTORS) would
  // otherwise sit there as a permanent "—", which reads as broken rather
  // than as the honest "no data" it actually is. Matches the same
  // live_tradable filter Terminal.js's own symbol picker already applies
  // -- this is the read-only strip's display-side equivalent, not a new
  // rule.
  const displaySymbols = mode === "live" ? symbols.filter((s) => s.live_tradable) : symbols;

  const staleThreshold = mode === "live" ? LIVE_TICKER_STALE_THRESHOLD_MS : DEFAULT_STALE_THRESHOLD_MS;
  const items = displaySymbols.map((s) => {
    const price = ticks[s.symbol];
    // Live mode's %-change is real-LTP-vs-real-previous-close
    // (liveCloses, from api.live.quotes); paper/virtual's own price AND
    // reference_price both come from the same simulated engine, so that
    // comparison stays internally consistent as-is. Never fall back to
    // s.reference_price for live -- that's the exact bug this fixes.
    const reference = mode === "live" ? liveCloses[s.symbol] : s.reference_price;
    const pct = price != null && reference ? ((price - reference) / reference) * 100 : null;
    const updatedAt = lastUpdatedAt[s.symbol];
    const stale = updatedAt === undefined || now - updatedAt > staleThreshold;
    return { symbol: s.symbol, price, pct, stale };
  });

  // Duplicated once so the CSS marquee can loop seamlessly (scroll exactly
  // one copy's width, then jump back with the second copy already in the
  // same visual position) -- a single copy would show a visible gap/snap
  // at the loop point instead of a continuous scroll.
  const track = [...items, ...items];

  return html`
    <div class="ticker">
      <div class="ticker-track">
        ${track.map((it, i) => html`
          <span key=${i} class=${`ticker-item ${it.stale && it.price != null ? "is-stale" : ""}`}>
            <span class="ticker-symbol">${it.symbol}</span>
            <span class="mono">${it.price != null ? fmtMoney(it.price) : "—"}</span>
            ${it.pct != null && html`
              <span class=${`mono ${it.pct >= 0 ? "pos" : "neg"}`}>
                ${it.pct >= 0 ? "+" : ""}${it.pct.toFixed(2)}%
              </span>
            `}
          </span>
        `)}
      </div>
    </div>
  `;
}
