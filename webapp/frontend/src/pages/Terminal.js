import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { fmtMoney } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";
import { OrderBook } from "../components/OrderBook.js";
import { OrderEntry } from "../components/OrderEntry.js";
import { AccountPanel } from "../components/AccountPanel.js";

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
  const [connected, setConnected] = React.useState(false);
  const [refreshKey, setRefreshKey] = React.useState(0);

  React.useEffect(() => {
    api.symbols().then((rows) => {
      setSymbols(rows);
      if (rows.length) setActive(rows[0].symbol);
    });
  }, []);

  React.useEffect(() => {
    if (!active) return;
    setConnected(false);
    const unsub = subscribeMarket(active, (tick) => {
      setConnected(true);
      setTicks((prev) => ({ ...prev, [active]: tick }));
    });
    return unsub;
  }, [active]);

  const tick = ticks[active];

  return html`
    <div class="page fade-in">
      <div style=${{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <div>
          <h1 style=${{ margin: 0, fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Terminal</h1>
          <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginTop: "2px" }}>Paper trading against the real bourse matching engine</div>
        </div>
        <div class="status-pill">
          <span class=${`status-dot ${connected ? "live" : "dead"}`} />
          ${connected ? "Live" : "Connecting…"}
        </div>
      </div>

      <${SymbolTabs} symbols=${symbols} active=${active} onSelect=${setActive} ticks=${ticks} />

      ${active && html`
        <div class="terminal-grid">
          <${OrderBook} tick=${tick} />
          <div class="panel panel-pad">
            <${CandleChart} symbol=${active} price=${tick?.price} />
          </div>
          <${OrderEntry} symbol=${active} price=${tick?.price} onOrderPlaced=${() => setRefreshKey((k) => k + 1)} />
        </div>
        <div style=${{ marginTop: "14px" }}>
          <${AccountPanel} refreshKey=${refreshKey} />
        </div>
      `}
    </div>
  `;
}
