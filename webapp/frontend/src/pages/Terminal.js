import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { fmtMoney } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";
import { OrderBook } from "../components/OrderBook.js";
import { OrderEntry } from "../components/OrderEntry.js";
import { AccountPanel } from "../components/AccountPanel.js";
import { TimeAndSales } from "../components/TimeAndSales.js";
import { DEFAULT_STALE_THRESHOLD_MS, useStaleness } from "../clock.js";

const STATUS_LABEL = { connecting: "Connecting…", live: "Live", reconnecting: "Reconnecting…", offline: "Offline" };

function SymbolTabs({ symbols, active, onSelect, ticks }) {
  return html`
    <div style=${{ display: "flex", gap: "8px", overflowX: "auto", paddingBottom: "4px", marginBottom: "16px" }}>
      ${symbols.map((s) => {
        const t = ticks[s.symbol];
        return html`
          <div key=${s.symbol} class=${`chip ${active === s.symbol ? "active" : ""}`} onClick=${() => onSelect(s.symbol)}>
            <span style=${{ fontWeight: 700 }}>${s.symbol}</span>
            ${t?.price && html`<span class="mono" style=${{ marginLeft: "8px", color: "var(--text-dim)" }}>${fmtMoney(t.price)}</span>`}
          </div>
        `;
      })}
    </div>
  `;
}

export function Terminal() {
  const [symbols, setSymbols] = React.useState([]);
  const [active, setActive] = React.useState(null);
  const [ticks, setTicks] = React.useState({});
  const [wsStatus, setWsStatus] = React.useState("connecting");
  const [lastTickAt, setLastTickAt] = React.useState(null);
  const [refreshKey, setRefreshKey] = React.useState(0);
  // {side, price, nonce} | null -- see OrderEntry.js's prefill prop
  // comment for why `nonce` matters (re-clicking a level with the same
  // side needs to re-apply, not be a no-op React skips as "unchanged").
  const [orderPrefill, setOrderPrefill] = React.useState(null);

  React.useEffect(() => {
    api.symbols().then((rows) => {
      setSymbols(rows);
      if (rows.length) setActive(rows[0].symbol);
    });
  }, []);

  React.useEffect(() => {
    if (!active) return;
    setWsStatus("connecting");
    setLastTickAt(null);
    const unsub = subscribeMarket(
      active,
      (tick) => {
        setLastTickAt(Date.now());
        setTicks((prev) => ({ ...prev, [active]: tick }));
      },
      setWsStatus,
    );
    return unsub;
  }, [active]);

  // Same derivation StatusBar.js uses: raw WS status alone can't tell a
  // connection that dropped a moment ago from one that's been down for a
  // minute (both report "reconnecting"), and tick arrival alone is a
  // one-way ratchet -- the PREVIOUS version of this badge set `connected`
  // to true on the first tick and never back to false, so it kept
  // claiming "Live" through a killed backend (found via Gap 5's actual
  // kill-the-backend test, tests/e2e/gap5_staleness.py). Elapsed time
  // since the last real tick (useStaleness) is what actually tells them
  // apart.
  const freshness = useStaleness(lastTickAt, DEFAULT_STALE_THRESHOLD_MS);
  let status;
  if (wsStatus === "live" && freshness === "fresh") status = "live";
  else if (lastTickAt === null) status = wsStatus;
  else if (freshness === "stale") status = "offline";
  else status = "reconnecting";

  const tick = ticks[active];

  return html`
    <div class="page fade-in">
      <div style=${{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <div>
          <h1 style=${{ margin: 0, fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Terminal</h1>
          <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginTop: "2px" }}>Paper trading against the real bourse matching engine</div>
        </div>
        <div class="status-pill">
          <span class=${`status-dot ${status === "live" ? "live" : "dead"}`} />
          ${STATUS_LABEL[status]}
        </div>
      </div>

      <${SymbolTabs} symbols=${symbols} active=${active} onSelect=${setActive} ticks=${ticks} />

      ${active && html`
        <div class="terminal-grid">
          <div style=${{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <${OrderBook} tick=${tick} stale=${status !== "live"}
                          onLevelClick=${(side, price) => setOrderPrefill({ side, price, nonce: Date.now() })} />
            <${TimeAndSales} tick=${tick} symbol=${active} />
          </div>
          <div class="panel panel-pad">
            <${CandleChart} symbol=${active} price=${tick?.price} stale=${status !== "live"} />
          </div>
          <${OrderEntry} symbol=${active} price=${tick?.price} prefill=${orderPrefill}
                         onOrderPlaced=${() => setRefreshKey((k) => k + 1)} />
        </div>
        <div style=${{ marginTop: "14px" }}>
          <${AccountPanel} refreshKey=${refreshKey} />
        </div>
      `}
    </div>
  `;
}
