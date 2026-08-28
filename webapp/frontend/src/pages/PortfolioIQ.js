import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useDensity } from "../theme.js";
import { DataTable } from "../components/DataTable.js";
import { BarChart } from "../components/BarChart.js";
import { MultiLineChart } from "../components/MultiLineChart.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { EmptyState } from "../components/EmptyState.js";
import { inr, pnl, pnlClass, pct, dash } from "../format.js";

function StatCard({ label, value, valueClass = "" }) {
  return html`
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class=${`stat-value mono ${valueClass}`}>${value}</div>
    </div>
  `;
}

function usePanel(fetchFn) {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState(null);
  const load = React.useCallback(() => {
    setError(null);
    fetchFn().then(setData).catch((e) => setError(e.message || "Could not load"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  React.useEffect(() => { load(); }, [load]);
  return { data, error, load };
}

function AttributionPanel() {
  const { data, error, load } = usePanel(api.portfolio.attribution);

  if (data === null && !error) return html`<div class="skeleton" style=${{ height: "260px" }} />`;
  if (error) return html`
    <div class="error-state">
      <div><div class="error-state-title">Could not load attribution</div><div class="error-state-detail">${error}</div></div>
      <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
    </div>
  `;
  if (!data.computable) return html`<${EmptyState} message=${`Not enough to attribute yet — ${data.reason}.`} />`;

  const effects = [
    { label: "Allocation", value: data.allocation },
    { label: "Selection", value: data.selection },
    { label: "Interaction", value: data.interaction },
  ];

  return html`
    <${React.Fragment}>
      <div style=${{ fontSize: "13px", color: "var(--text-dim)", marginBottom: "12px" }}>
        Benchmark: <strong style=${{ color: "var(--text)" }}>${data.benchmark_name}</strong>
      </div>
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px", marginBottom: "16px" }} class="dash-stats">
        <${StatCard} label="Portfolio Return" value=${pct(data.portfolio_return)} valueClass=${pnlClass(data.portfolio_return)} />
        <${StatCard} label="Benchmark Return" value=${pct(data.benchmark_return)} valueClass=${pnlClass(data.benchmark_return)} />
        <${StatCard} label="Excess Return" value=${pct(data.excess)} valueClass=${pnlClass(data.excess)} />
      </div>
      <div class="panel-title" style=${{ fontSize: "12px" }}>Brinson-Fachler Decomposition</div>
      <${BarChart} data=${effects} height=${160} />
      <div style=${{ fontSize: "11.5px", color: "var(--text-faint)", marginTop: "10px", lineHeight: 1.5 }}>${data.methodology_note}</div>
    <//>
  `;
}

function RealizedPnlPanel() {
  const { data, error, load } = usePanel(api.portfolio.realizedPnlCurve);

  if (data === null && !error) return html`<div class="skeleton" style=${{ height: "260px" }} />`;
  if (error) return html`
    <div class="error-state">
      <div><div class="error-state-title">Could not load the realized P&L walk</div><div class="error-state-detail">${error}</div></div>
      <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
    </div>
  `;
  if (!data.length) return html`<${EmptyState} message="No filled trades yet — the realized P&L walk starts at your first fill." />`;

  const points = data.map((p) => p.realized_pnl);
  const latest = points[points.length - 1];

  return html`
    <${React.Fragment}>
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "10px" }}>
        <div style=${{ fontSize: "12px", color: "var(--text-faint)" }}>${data.length} fill${data.length === 1 ? "" : "s"}</div>
        <div class=${`mono ${pnlClass(latest)}`} style=${{ fontWeight: 700 }}>${pnl(latest)}</div>
      </div>
      <${MultiLineChart} series=${[{ name: "Realized P&L", points, color: "var(--accent-bright)" }]} yFormat=${(v) => inr(v, { decimals: 0 })} height=${220} />
      <div style=${{ fontSize: "11.5px", color: "var(--text-faint)", marginTop: "10px" }}>
        Realized P&L only, walked fill-by-fill — not mark-to-market. This will not match the Dashboard's equity curve while a position is open; see the Dashboard for the mark-to-market view.
      </div>
    <//>
  `;
}

function SubAccountsPanel() {
  const density = useDensity();
  const { data, error, load } = usePanel(api.portfolio.subAccounts);

  if (data === null && !error) return html`<div class="skeleton" style=${{ height: "200px" }} />`;
  if (error) return html`
    <div class="error-state">
      <div><div class="error-state-title">Could not load sub-accounts</div><div class="error-state-detail">${error}</div></div>
      <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
    </div>
  `;
  if (!data.length) return html`<${EmptyState} message="No sub-accounts configured." />`;

  const columns = [
    { key: "label", label: "Sub-Account" },
    { key: "is_active", label: "Status", render: (r) => html`<span class=${`badge ${r.is_active ? "badge-live" : "badge-off"}`}>${r.is_active ? "active" : "inactive"}</span>` },
    { key: "sizing_multiplier", label: "Sizing", align: "right", render: (r) => `${r.sizing_multiplier}×` },
    { key: "cash", label: "Cash", align: "right", render: (r) => inr(r.cash) },
    { key: "total_value", label: "Total Value", align: "right", render: (r) => inr(r.total_value) },
    { key: "total_realized_pnl", label: "Realized", align: "right", render: (r) => html`<span class=${pnlClass(r.total_realized_pnl)}>${pnl(r.total_realized_pnl)}</span>` },
    { key: "total_unrealized_pnl", label: "Unrealized", align: "right", render: (r) => html`<span class=${pnlClass(r.total_unrealized_pnl)}>${pnl(r.total_unrealized_pnl)}</span>` },
  ];

  return html`<${DataTable} columns=${columns} rows=${data} rowKey="id" density=${density} />`;
}

export function PortfolioIQ() {
  return html`
    <div class="page fade-in">
      <div style=${{ marginBottom: "20px" }}>
        <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Portfolio IQ</h1>
        <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>Attribution against a named benchmark, the realized P&L walk, and the sub-account breakdown.</div>
      </div>

      <div class="panel panel-pad" style=${{ marginBottom: "16px" }}>
        <div class="panel-title">Attribution</div>
        <${ErrorBoundary} label="Attribution"><${AttributionPanel} /><//>
      </div>

      <div class="panel panel-pad" style=${{ marginBottom: "16px" }}>
        <div class="panel-title">Realized P&L</div>
        <${ErrorBoundary} label="Realized P&L"><${RealizedPnlPanel} /><//>
      </div>

      <div class="panel panel-pad">
        <div class="panel-title">Sub-Accounts</div>
        <${ErrorBoundary} label="Sub-Accounts"><${SubAccountsPanel} /><//>
      </div>
    </div>
  `;
}
