import React from "react";
import { html } from "../html.js";
import { fmtMoney } from "../format.js";

const RING_SIZE = 150; // caps both memory AND rendered DOM nodes -- an
// unbounded tape is a leak that only surfaces after the terminal's been
// open an hour, by which point it's a much harder bug to reproduce.

// NOT a real fill-by-fill trade log: market_ws.py's tick payload is a
// book/price SNAPSHOT (see _tick_payload in app/routers/market_ws.py),
// not a stream of discrete executed-trade events with real sizes. There
// is no per-trade quantity to show here, and fabricating one would be
// exactly the kind of invented number this codebase's whole measurement
// discipline exists to avoid. What IS real: every row here is an actual
// observed price change between two consecutive ticks, direction inferred
// by the classic tick rule (price rose = buy-initiated, fell = sell-
// initiated) -- labeled "Price Changes", not "Trades", so it never claims
// to be something it isn't.
export function TimeAndSales({ tick, symbol }) {
  const [rows, setRows] = React.useState([]);
  const prevPriceRef = React.useRef(null);

  React.useEffect(() => {
    prevPriceRef.current = null;
    setRows([]);
  }, [symbol]);

  React.useEffect(() => {
    const price = tick?.price;
    if (price === null || price === undefined) return;
    const prev = prevPriceRef.current;
    prevPriceRef.current = price;
    if (prev === null || price === prev) return; // first tick, or no change -- nothing to log
    setRows((r) => {
      const next = [{ price, direction: price > prev ? "up" : "down", timestamp: Date.now() }, ...r];
      return next.length > RING_SIZE ? next.slice(0, RING_SIZE) : next;
    });
  }, [tick]);

  return html`
    <div class="panel panel-pad" style=${{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div class="panel-title">Time & Sales <span style=${{ fontWeight: 400, color: "var(--text-faint)", fontSize: "10px", textTransform: "none" }}>(price changes, not fills -- see below)</span></div>
      <div style=${{ overflowY: "auto", flex: 1, maxHeight: "360px" }}>
        ${rows.length === 0
          ? html`<div style=${{ color: "var(--text-faint)", fontSize: "12px", padding: "16px 0", textAlign: "center" }}>No price changes yet</div>`
          : rows.map((r, i) => html`
              <div key=${i} class="row hairline" style=${{ padding: "3px 4px", minHeight: "auto" }}>
                <span class=${`mono ${r.direction === "up" ? "pos" : "neg"}`}>${fmtMoney(r.price)}</span>
                <span class="mono" style=${{ color: "var(--text-faint)", fontSize: "10.5px" }}>
                  ${new Date(r.timestamp).toLocaleTimeString("en-IN", { hour12: false })}
                </span>
              </div>
            `)}
      </div>
    </div>
  `;
}
