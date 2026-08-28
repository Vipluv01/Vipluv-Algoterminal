import React from "react";
import { html } from "../html.js";
import { api, subscribeMarketForMode } from "../api.js";
import { fmtMoney } from "../format.js";
import { DEFAULT_STALE_THRESHOLD_MS, useNow } from "../clock.js";
import { useMode } from "../mode.js";

// A live scrolling ticker strip across every named instrument -- every
// other page in algoterminal already proves the real-time data exists
// (Terminal/Charts subscribe per-symbol already); this is the same
// subscribeMarketForMode() primitive, just fanned out across all 7
// symbols at once and rendered as a continuously-scrolling marquee
// instead of a single price display, matching a real trading terminal's
// index bar.
export function Ticker() {
  const [symbols, setSymbols] = React.useState([]);
  const [ticks, setTicks] = React.useState({});
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
    // is_derived: true), but /ws/market/{symbol} only streams the
    // NAMED_INSTRUMENTS constituents each is computed FROM (there's no
    // live per-tick feed for a derived index itself). Subscribing to one
    // anyway isn't a graceful no-op: market_ws.py rejects an unknown
    // symbol by calling websocket.close() before accept(), which Starlette
    // turns into an HTTP 403 at the handshake -- a real, visible connection
    // failure every single page load, not a quiet skip. Filtered out here;
    // a derived index's price/pct just render as the dash sentinel below
    // rather than a number this ticker was never actually fed.
    //
    // In live mode this opens N SIMULTANEOUS real Angel One WebSocket
    // connections (one per named instrument) purely for a decorative
    // strip -- fine for the simulated engine (unlimited fake connections
    // cost nothing) but worth someone revisiting against Angel One's
    // actual per-account WS connection limits before this ships live for
    // real; flagged, not silently worked around here.
    const unsubs = symbols
      .filter((s) => !s.is_derived)
      .map((s) =>
        subscribeMarketForMode(mode, s.symbol, (tick) => {
          setTicks((prev) => ({ ...prev, [s.symbol]: tick.price }));
          setLastUpdatedAt((prev) => ({ ...prev, [s.symbol]: Date.now() }));
        })
      );
    return () => unsubs.forEach((u) => u());
  }, [symbols, mode]);

  if (!symbols.length) return null;

  const items = symbols.map((s) => {
    const price = ticks[s.symbol];
    const pct = price != null ? ((price - s.reference_price) / s.reference_price) * 100 : null;
    const updatedAt = lastUpdatedAt[s.symbol];
    const stale = updatedAt === undefined || now - updatedAt > DEFAULT_STALE_THRESHOLD_MS;
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
