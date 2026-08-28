import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useToast } from "../toast.js";
import { useDensity } from "../theme.js";
import { DataTable } from "../components/DataTable.js";
import { PayoffDiagram } from "../components/PayoffDiagram.js";
import { Heatmap } from "../components/Heatmap.js";
import { EmptyState } from "../components/EmptyState.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { fmtNum, px, pct, dash } from "../format.js";

// The task this screen exists to be honest about: every OTHER fill in this
// app is a real match from bourse's Go matching engine. Options are not --
// there is no options order book, so a "fill" here is a theoretical BSM
// price plus a modeled half-spread. Stated plainly on the screen itself
// (not a tooltip) is engineering judgment; found by someone who wasn't
// told is concealment. See app/options/execution.py's EXECUTION_NOTICE,
// which travels on every individual order response too -- this banner is
// the same fact, just always-visible rather than per-fill.
const PERSISTENT_NOTICE =
  "Model-priced synthetic execution — BSM theoretical + modeled half-spread. Not matched by the order book.";

const UNDERLYING_KEY = "algoterminal:options:underlying";

function readStoredUnderlying() {
  try {
    return window.localStorage.getItem(UNDERLYING_KEY);
  } catch {
    return null;
  }
}

function writeStoredUnderlying(v) {
  try {
    window.localStorage.setItem(UNDERLYING_KEY, v);
  } catch {
    /* non-fatal */
  }
}

function ChainPanel({ underlying, expiry, onAddLeg }) {
  const [chain, setChain] = React.useState(null);
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() => {
    if (!underlying || !expiry) return;
    setError(null);
    setChain(null);
    api.options.chain(underlying, expiry).then(setChain).catch((e) => setError(e.message || "Could not load the chain"));
  }, [underlying, expiry]);
  React.useEffect(() => { load(); }, [load]);

  if (!underlying || !expiry) return html`<${EmptyState} message="Pick an underlying and expiry to see the chain." />`;
  if (chain === null && !error) return html`<div class="skeleton" style=${{ height: "320px" }} />`;
  if (error) return html`
    <div class="error-state">
      <div><div class="error-state-title">Could not load the chain</div><div class="error-state-detail">${error}</div></div>
      <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
    </div>
  `;

  const atmStrike = chain.rows.reduce(
    (best, r) => (Math.abs(r.strike - chain.spot) < Math.abs(best - chain.spot) ? r.strike : best),
    chain.rows[0]?.strike ?? 0,
  );

  const columns = [
    { key: "call_oi", label: "OI", align: "right", render: (r) => fmtNum(r.call.open_interest) },
    { key: "call_vol", label: "Vol", align: "right", render: (r) => fmtNum(r.call.volume) },
    { key: "call_iv", label: "IV", align: "right", render: (r) => pct(r.call.iv) },
    {
      key: "call_px", label: "Call", align: "right",
      render: (r) => html`<button class="btn btn-sm btn-ghost mono" onClick=${() => onAddLeg(r.call, "CE")}>${px(r.call.theoretical_price)}</button>`,
    },
    {
      key: "strike", label: "Strike",
      render: (r) => html`
        <span style=${{ fontWeight: 700, display: "flex", justifyContent: "center", alignItems: "center", gap: "6px" }}>
          ${fmtNum(r.strike)}
          ${r.strike === atmStrike && html`<span class="badge badge-accent">ATM</span>`}
        </span>
      `,
    },
    {
      key: "put_px", label: "Put", align: "right",
      render: (r) => html`<button class="btn btn-sm btn-ghost mono" onClick=${() => onAddLeg(r.put, "PE")}>${px(r.put.theoretical_price)}</button>`,
    },
    { key: "put_iv", label: "IV", align: "right", render: (r) => pct(r.put.iv) },
    { key: "put_vol", label: "Vol", align: "right", render: (r) => fmtNum(r.put.volume) },
    { key: "put_oi", label: "OI", align: "right", render: (r) => fmtNum(r.put.open_interest) },
  ];

  return html`
    <${React.Fragment}>
      <div style=${{ display: "flex", gap: "16px", marginBottom: "12px", fontSize: "12px", color: "var(--text-faint)" }}>
        <span>Spot: <strong class="mono" style=${{ color: "var(--text)" }}>${px(chain.spot)}</strong></span>
        <span>Expiry: <strong style=${{ color: "var(--text)" }}>${chain.expiry_label}</strong></span>
        <span>Click a Call/Put price to add it to the builder below.</span>
      </div>
      <${DataTable} columns=${columns} rows=${chain.rows} rowKey="strike" />
    <//>
  `;
}

function LegBuilder({ legs, spot, onUpdateLeg, onRemoveLeg, onSubmit, submitting }) {
  if (!legs.length) {
    return html`<${EmptyState} message="No legs yet — click a Call or Put price above to build a position." />`;
  }

  const payoffLegs = legs.map((l) => ({
    type: l.option_type === "CE" ? "call" : "put",
    strike: l.strike,
    premium: l.theoretical_price,
    qty: l.side === "buy" ? l.qty : -l.qty,
  }));

  return html`
    <${React.Fragment}>
      <div style=${{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "16px" }}>
        ${legs.map((l) => html`
          <div key=${l.key} class="row hairline" style=${{ alignItems: "center", gap: "10px" }}>
            <span class="mono" style=${{ minWidth: "160px" }}>${l.option_type} ${fmtNum(l.strike)} @ ${px(l.theoretical_price)}</span>
            <div class="toggle-row" style=${{ width: "auto" }}>
              <button class=${`btn btn-sm ${l.side === "buy" ? "active neutral" : ""}`} onClick=${() => onUpdateLeg(l.key, { side: "buy" })}>Buy</button>
              <button class=${`btn btn-sm ${l.side === "sell" ? "active neutral" : ""}`} onClick=${() => onUpdateLeg(l.key, { side: "sell" })}>Sell</button>
            </div>
            <input class="input" type="number" min="1" style=${{ width: "80px" }} value=${l.qty}
                   onInput=${(e) => onUpdateLeg(l.key, { qty: Math.max(1, Number(e.target.value) || 1) })} />
            <div style=${{ flex: 1 }} />
            <button class="btn btn-sm btn-ghost" onClick=${() => onRemoveLeg(l.key)} aria-label="Remove leg">✕</button>
          </div>
        `)}
      </div>
      <${PayoffDiagram} legs=${payoffLegs} spot=${spot} />
      <div style=${{ marginTop: "14px" }}>
        <button class="btn btn-primary" onClick=${onSubmit} disabled=${submitting}>
          ${submitting ? "Submitting…" : `Submit ${legs.length} Leg${legs.length === 1 ? "" : "s"}`}
        </button>
      </div>
    <//>
  `;
}

function fmtGreek(v) {
  return v.toFixed(4);
}

function GreeksPanel({ reloadSignal }) {
  const density = useDensity();
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() => {
    setError(null);
    api.options.greeks().then(setData).catch((e) => setError(e.message || "Could not load Greeks"));
  }, []);
  // reloadSignal is bumped by the parent right after an order submission
  // -- Greeks/positions/stress all change the moment a leg fills, and
  // there's no push channel for that, so the parent just asks for a
  // refetch the same way a page-level "load()" button would.
  React.useEffect(() => { load(); }, [load, reloadSignal]);

  if (data === null && !error) return html`<div class="skeleton" style=${{ height: "220px" }} />`;
  if (error) return html`
    <div class="error-state">
      <div><div class="error-state-title">Could not load Greeks</div><div class="error-state-detail">${error}</div></div>
      <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
    </div>
  `;

  const positionColumns = [
    { key: "symbol", label: "Contract" },
    { key: "qty", label: "Qty", align: "right" },
    { key: "delta", label: "Delta", align: "right", render: (r) => fmtGreek(r.greeks.delta) },
    { key: "gamma", label: "Gamma", align: "right", render: (r) => fmtGreek(r.greeks.gamma) },
    { key: "theta", label: "Theta", align: "right", render: (r) => fmtGreek(r.greeks.theta) },
    { key: "vega", label: "Vega", align: "right", render: (r) => fmtGreek(r.greeks.vega) },
    { key: "rho", label: "Rho", align: "right", render: (r) => fmtGreek(r.greeks.rho) },
  ];

  const stressCols = data.stress[0]?.rows.map((r) => `${(r.shift_pct * 100).toFixed(0)}%`) ?? [];
  const stressRows = data.stress.map((s) => s.underlying);
  const stressValues = data.stress.map((s) => s.rows.map((r) => r.pnl));

  return html`
    <${React.Fragment}>
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "12px", marginBottom: "18px" }} class="dash-stats">
        <div class="stat-card"><div class="stat-label">Delta</div><div class="stat-value mono">${fmtGreek(data.aggregate.delta)}</div></div>
        <div class="stat-card"><div class="stat-label">Gamma</div><div class="stat-value mono">${fmtGreek(data.aggregate.gamma)}</div></div>
        <div class="stat-card"><div class="stat-label">Theta</div><div class="stat-value mono">${fmtGreek(data.aggregate.theta)}</div></div>
        <div class="stat-card"><div class="stat-label">Vega</div><div class="stat-value mono">${fmtGreek(data.aggregate.vega)}</div></div>
        <div class="stat-card"><div class="stat-label">Rho</div><div class="stat-value mono">${fmtGreek(data.aggregate.rho)}</div></div>
      </div>

      <div class="panel-title" style=${{ fontSize: "12px" }}>Positions</div>
      ${data.positions.length === 0
        ? html`<${EmptyState} message="No open option positions." />`
        : html`<${DataTable} columns=${positionColumns} rows=${data.positions} rowKey="symbol" density=${density} />`}

      <div class="panel-title" style=${{ fontSize: "12px", marginTop: "18px" }}>Stress Matrix</div>
      ${data.stress.length === 0
        ? html`<${EmptyState} message="No option positions to stress -- open one above to see P&L across a spot-price shock grid." />`
        : html`<${Heatmap} rows=${stressRows} cols=${stressCols} values=${stressValues} format=${(v) => px(v)} />`}
    <//>
  `;
}

export function Options() {
  const [underlyings, setUnderlyings] = React.useState([]);
  const [underlying, setUnderlyingState] = React.useState(readStoredUnderlying);
  const [expiries, setExpiries] = React.useState([]);
  const [expiry, setExpiry] = React.useState(null);
  const [legs, setLegs] = React.useState([]);
  const [submitting, setSubmitting] = React.useState(false);
  const [chainSpot, setChainSpot] = React.useState(null);
  const [greeksReloadTick, setGreeksReloadTick] = React.useState(0);
  const toast = useToast();

  React.useEffect(() => {
    api.symbols().then((rows) => setUnderlyings(rows.map((r) => r.symbol))).catch(() => setUnderlyings([]));
    api.options.expiries().then((rows) => {
      setExpiries(rows);
      if (rows.length) setExpiry((prev) => prev || rows[0].date);
    }).catch(() => setExpiries([]));
  }, []);

  // Default/repair the underlying once the real list is known -- a stored
  // value from a previous session that's no longer a valid underlying
  // (or none stored yet) falls back to the first available rather than
  // silently requesting a chain for a symbol that doesn't exist.
  React.useEffect(() => {
    if (!underlyings.length) return;
    if (!underlying || !underlyings.includes(underlying)) {
      setUnderlyingState(underlyings[0]);
    }
  }, [underlyings, underlying]);

  function setUnderlying(u) {
    setUnderlyingState(u);
    writeStoredUnderlying(u);
    setLegs([]); // a leg priced/expiring under the OLD underlying makes no sense once it changes
  }

  // Refetch the chain's spot separately (for the payoff diagram) whenever
  // underlying/expiry change -- ChainPanel already fetches the same data
  // for its own table; this is cheap and keeps the two rendering
  // independently without prop-drilling the whole chain object down.
  React.useEffect(() => {
    if (!underlying || !expiry) return;
    api.options.chain(underlying, expiry).then((c) => setChainSpot(c.spot)).catch(() => {});
  }, [underlying, expiry]);

  function addLeg(quote, optionType) {
    setLegs((prev) => [...prev, {
      key: `${quote.contract_key}-${Date.now()}-${Math.random()}`,
      contract_key: quote.contract_key, option_type: optionType, strike: quote.strike,
      theoretical_price: quote.theoretical_price, side: "buy", qty: 1,
    }]);
  }

  function updateLeg(key, patch) {
    setLegs((prev) => prev.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }

  function removeLeg(key) {
    setLegs((prev) => prev.filter((l) => l.key !== key));
  }

  async function submitLegs() {
    setSubmitting(true);
    let okCount = 0;
    for (const leg of legs) {
      try {
        const order = await api.options.submitOrder({
          underlying, option_type: leg.option_type, strike: leg.strike, expiry, side: leg.side, qty: leg.qty,
        });
        okCount++;
        toast(`${leg.option_type} ${leg.strike} ${leg.side} @ ${px(order.avg_fill_px ?? 0)} — ${order.execution_notice}`, "ok");
      } catch (e) {
        toast(`${leg.option_type} ${leg.strike}: ${e.message || "order failed"}`, "err");
      }
    }
    setSubmitting(false);
    if (okCount === legs.length) setLegs([]);
    setGreeksReloadTick((t) => t + 1);
  }

  return html`
    <div class="page fade-in">
      <div style=${{ marginBottom: "16px" }}>
        <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Options</h1>
        <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>Chain, multi-leg builder, and portfolio Greeks.</div>
      </div>

      <div class="notice-banner">
        <strong>Synthetic execution:</strong> ${PERSISTENT_NOTICE}
      </div>

      <div style=${{ display: "flex", gap: "10px", marginBottom: "16px", flexWrap: "wrap" }}>
        <select class="input" style=${{ maxWidth: "180px", fontWeight: 700 }} value=${underlying || ""} onChange=${(e) => setUnderlying(e.target.value)}>
          ${underlyings.map((u) => html`<option key=${u} value=${u}>${u}</option>`)}
        </select>
        <select class="input" style=${{ maxWidth: "180px" }} value=${expiry || ""} onChange=${(e) => setExpiry(e.target.value)}>
          ${expiries.map((ex) => html`<option key=${ex.date} value=${ex.date}>${ex.label} (${ex.kind})</option>`)}
        </select>
      </div>

      <div class="panel panel-pad" style=${{ marginBottom: "16px" }}>
        <div class="panel-title">Chain</div>
        <${ErrorBoundary} label="Option Chain"><${ChainPanel} underlying=${underlying} expiry=${expiry} onAddLeg=${addLeg} /><//>
      </div>

      <div class="panel panel-pad" style=${{ marginBottom: "16px" }}>
        <div class="panel-title">Position Builder</div>
        <${ErrorBoundary} label="Position Builder">
          <${LegBuilder} legs=${legs} spot=${chainSpot} onUpdateLeg=${updateLeg} onRemoveLeg=${removeLeg} onSubmit=${submitLegs} submitting=${submitting} />
        <//>
      </div>

      <div class="panel panel-pad">
        <div class="panel-title">Greeks & Stress</div>
        <${ErrorBoundary} label="Greeks"><${GreeksPanel} reloadSignal=${greeksReloadTick} /><//>
      </div>
    </div>
  `;
}
