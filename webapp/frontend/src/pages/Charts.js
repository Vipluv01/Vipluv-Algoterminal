import React from "react";
import { html } from "../html.js";
import { api, subscribeMarketForMode } from "../api.js";
import { fmtMoney } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";
import { useMode } from "../mode.js";

const LAYOUT_KEY = "algoterminal:charts:layout";
const PANES_KEY = "algoterminal:charts:panes";
const PANE_COUNT = { "1": 1, "1x2": 2, "2x2": 4 };
const DEFAULT_PANES = [
  { symbol: "ICICIBANK", interval: "1m" },
  { symbol: "HDFCBANK", interval: "1m" },
  { symbol: "RELIANCE", interval: "1m" },
  { symbol: "TCS", interval: "1m" },
];

function readStoredLayout() {
  try {
    const v = window.localStorage.getItem(LAYOUT_KEY);
    return v && v in PANE_COUNT ? v : "2x2";
  } catch {
    return "2x2";
  }
}

function readStoredPanes() {
  try {
    const raw = window.localStorage.getItem(PANES_KEY);
    if (!raw) return DEFAULT_PANES;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length === 4 && parsed.every((p) => p && p.symbol)) return parsed;
    return DEFAULT_PANES;
  } catch {
    return DEFAULT_PANES;
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch {
    /* non-fatal, matches theme.js/mode.js's own storage fallback */
  }
}

function ChartSlot({ pane, allSymbols, onChangeSymbol, onChangeInterval, onCrosshairMove, syncCrosshair }) {
  const [tick, setTick] = React.useState(null);
  const mode = useMode();
  React.useEffect(() => {
    const unsub = subscribeMarketForMode(mode, pane.symbol, setTick);
    return unsub;
  }, [pane.symbol, mode]);

  return html`
    <div class="panel panel-pad">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
        <select class="input" style=${{ width: "auto", fontWeight: 700 }} value=${pane.symbol} onChange=${(e) => onChangeSymbol(e.target.value)}>
          ${allSymbols.map((s) => html`<option key=${s} value=${s}>${s}</option>`)}
        </select>
        ${tick?.price && html`<span class="mono" style=${{ fontWeight: 600 }}>${fmtMoney(tick.price)}</span>`}
      </div>
      <${CandleChart} symbol=${pane.symbol} price=${tick?.price}
                       initialIntervalKey=${pane.interval} onIntervalChange=${onChangeInterval}
                       onCrosshairMove=${onCrosshairMove} syncCrosshair=${syncCrosshair} />
    </div>
  `;
}

const LAYOUTS = [
  { key: "1", label: "1" },
  { key: "1x2", label: "1×2" },
  { key: "2x2", label: "2×2" },
];

export function Charts() {
  const [allSymbols, setAllSymbols] = React.useState([]);
  const [layout, setLayoutRaw] = React.useState(readStoredLayout);
  const [panes, setPanesRaw] = React.useState(readStoredPanes);
  // The last dataIndex any pane's crosshair moved to, broadcast to every
  // visible pane (including the one that produced it -- see CandleChart's
  // executeAction effect for why that's safe/idempotent rather than a
  // feedback loop). null clears every pane's crosshair together, e.g. when
  // the mouse leaves the chart area entirely.
  const [sharedCrosshair, setSharedCrosshair] = React.useState(null);

  React.useEffect(() => {
    api.symbols().then((rows) => setAllSymbols(rows.map((r) => r.symbol)));
  }, []);

  function setLayout(next) {
    setLayoutRaw(next);
    writeStored(LAYOUT_KEY, next);
  }

  function setPane(i, patch) {
    setPanesRaw((prev) => {
      const next = prev.map((p, idx) => (idx === i ? { ...p, ...patch } : p));
      writeStored(PANES_KEY, next);
      return next;
    });
  }

  if (!allSymbols.length) return html`<div class="page"><div class="skeleton" style=${{ height: "400px" }} /></div>`;

  const visibleCount = PANE_COUNT[layout];
  // .charts-grid's existing repeat(2, 1fr) already produces exactly what
  // "1x2" needs (a single row of 2) when there are only 2 children, and
  // what "2x2" needs (2 rows of 2) when there are 4 -- no separate layout
  // class required for either.
  const gridClass = layout === "1" ? "" : "charts-grid";

  return html`
    <div class="page fade-in">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: "10px", marginBottom: "18px" }}>
        <div>
          <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Charts</h1>
          <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>Watch up to 4 instruments side by side, crosshairs synced, layout and per-pane symbols persist</div>
        </div>
        <div class="toggle-row" style=${{ width: "auto" }} role="group" aria-label="Chart layout">
          ${LAYOUTS.map((l) => html`
            <button key=${l.key} class=${`btn btn-sm ${layout === l.key ? "active neutral" : ""}`} onClick=${() => setLayout(l.key)}>${l.label}</button>
          `)}
        </div>
      </div>
      <div class=${gridClass} style=${layout === "1" ? { display: "block" } : undefined}>
        ${panes.slice(0, visibleCount).map((pane, i) => html`
          <${ChartSlot} key=${i} pane=${pane} allSymbols=${allSymbols}
                        onChangeSymbol=${(sym) => setPane(i, { symbol: sym })}
                        onChangeInterval=${(interval) => setPane(i, { interval })}
                        onCrosshairMove=${setSharedCrosshair}
                        syncCrosshair=${sharedCrosshair} />
        `)}
      </div>
    </div>
  `;
}
