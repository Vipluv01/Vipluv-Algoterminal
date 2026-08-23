import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { fmtMoney } from "../format.js";

// A live scrolling ticker strip across every named instrument -- every
// other page in algoterminal already proves the real-time data exists
// (Terminal/Charts subscribe per-symbol already); this is the same
// subscribeMarket() primitive, just fanned out across all 7 symbols at
// once and rendered as a continuously-scrolling marquee instead of a
// single price display, matching a real trading terminal's index bar.
export function Ticker() {
  const [symbols, setSymbols] = React.useState([]);
  const [ticks, setTicks] = React.useState({});

  React.useEffect(() => { api.symbols().then(setSymbols); }, []);

  React.useEffect(() => {
    if (!symbols.length) return;
    const unsubs = symbols.map((s) =>
      subscribeMarket(s.symbol, (tick) => {
        setTicks((prev) => ({ ...prev, [s.symbol]: tick.price }));
      })
    );
    return () => unsubs.forEach((u) => u());
  }, [symbols]);

  if (!symbols.length) return null;

  const items = symbols.map((s) => {
    const price = ticks[s.symbol];
    const pct = price != null ? ((price - s.reference_price) / s.reference_price) * 100 : null;
    return { symbol: s.symbol, price, pct };
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
          <span key=${i} class="ticker-item">
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
