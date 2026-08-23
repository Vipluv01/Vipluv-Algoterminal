import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { fmtMoney } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";

const DEFAULT_SLOTS = ["ICICIBANK", "HDFCBANK", "RELIANCE", "TCS"];

function ChartSlot({ symbol, allSymbols, onChange }) {
  const [tick, setTick] = React.useState(null);
  React.useEffect(() => {
    const unsub = subscribeMarket(symbol, setTick);
    return unsub;
  }, [symbol]);

  return html`
    <div class="panel panel-pad">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
        <select class="input" style=${{ width: "auto", fontWeight: 700 }} value=${symbol} onChange=${(e) => onChange(e.target.value)}>
          ${allSymbols.map((s) => html`<option key=${s} value=${s}>${s}</option>`)}
        </select>
        ${tick?.price && html`<span class="mono" style=${{ fontWeight: 600 }}>${fmtMoney(tick.price)}</span>`}
      </div>
      <${CandleChart} symbol=${symbol} price=${tick?.price} />
    </div>
  `;
}

export function Charts() {
  const [allSymbols, setAllSymbols] = React.useState([]);
  const [slots, setSlots] = React.useState(DEFAULT_SLOTS);

  React.useEffect(() => {
    api.symbols().then((rows) => setAllSymbols(rows.map((r) => r.symbol)));
  }, []);

  function setSlot(i, symbol) {
    setSlots((prev) => prev.map((s, idx) => (idx === i ? symbol : s)));
  }

  if (!allSymbols.length) return html`<div class="page"><div class="skeleton" style=${{ height: "400px" }} /></div>`;

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Charts</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "18px" }}>Watch up to 4 instruments side by side, each independently switchable</div>
      <div class="charts-grid">
        ${slots.map((s, i) => html`<${ChartSlot} key=${i} symbol=${s} allSymbols=${allSymbols} onChange=${(sym) => setSlot(i, sym)} />`)}
      </div>
    </div>
  `;
}
