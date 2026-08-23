import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtPct } from "../format.js";

function StatCard({ label, value, sub }) {
  return html`
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class="stat-value mono">${value}</div>
      ${sub && html`<div class="stat-sub">${sub}</div>`}
    </div>
  `;
}

function WeightBar({ label, weight, rank }) {
  return html`
    <div style=${{ marginBottom: "14px" }}>
      <div style=${{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12.5px" }}>
        <span style=${{ fontWeight: 600 }}>${label}</span>
        <span class="mono">${fmtPct(weight)}</span>
      </div>
      <div style=${{ height: "8px", background: "var(--surface-2)", borderRadius: "5px", overflow: "hidden" }}>
        <div style=${{
          width: `${Math.max(weight * 100, 1)}%`, height: "100%", borderRadius: "5px",
          background: rank === 0 ? "linear-gradient(90deg, var(--accent), var(--accent-bright))" : "var(--violet)",
        }} />
      </div>
    </div>
  `;
}

export function Optimizer() {
  const [data, setData] = React.useState(null);
  const [strategies, setStrategies] = React.useState([]);

  const load = React.useCallback(() => {
    Promise.all([api.optimizer(), api.strategies.list()]).then(([opt, strats]) => {
      setData(opt);
      setStrategies(strats);
    });
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const nameByKey = Object.fromEntries(strategies.map((s) => [s.key, s.name]));

  const ranked = data && !data.insufficient_history
    ? data.strategy_keys
        .map((key, i) => ({ key, weight: data.weights[i] }))
        .sort((a, b) => b.weight - a.weight)
    : [];

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Optimizer</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>
        Max-Sharpe allocation across every strategy, computed from this account's own real paper-trading P&L — not a synthetic backtest.
      </div>

      ${!data
        ? html`<div class="skeleton" style=${{ height: "220px" }} />`
        : data.insufficient_history
          ? html`
            <div class="panel panel-pad" style=${{ textAlign: "center", color: "var(--text-faint)", padding: "40px" }}>
              Not enough real trade history yet — a max-Sharpe allocation needs realized P&L from at least 2 strategies
              across ${data.min_trading_days_required}+ distinct trading days before the estimate means anything.
              Enable a couple of strategies on the Strategies page and let them trade for a while.
            </div>
          `
          : html`
            <div class="dash-grid">
              <div class="panel panel-pad">
                <div class="panel-title">Suggested Allocation</div>
                ${ranked.map((r, i) => html`
                  <${WeightBar} key=${r.key} label=${nameByKey[r.key] || r.key} weight=${r.weight} rank=${i} />
                `)}
              </div>

              <div>
                <div style=${{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "14px" }} class="dash-stats">
                  <${StatCard} label="Expected Return" value=${data.expected_return !== null ? fmtPct(data.expected_return) : "—"} sub="annualized" />
                  <${StatCard} label="Expected Volatility" value=${data.expected_volatility !== null ? fmtPct(data.expected_volatility) : "—"} sub="annualized" />
                  <${StatCard} label="Sharpe Ratio" value=${data.sharpe_ratio !== null ? data.sharpe_ratio.toFixed(2) : "—"} />
                  <${StatCard} label="Trading Days Used" value=${data.days_of_history} />
                </div>
                <div class="panel panel-pad">
                  <div class="panel-title">Methodology</div>
                  <p style=${{ color: "var(--text-dim)", fontSize: "12px", lineHeight: 1.6, margin: 0 }}>
                    Standard mean-variance optimization (Markowitz 1952) over each strategy's own realized daily
                    P&L — deliberately the boring, textbook version, no shrinkage or leverage. The estimate only gets
                    more meaningful as more real trading days accumulate; it is not a promise of future performance.
                  </p>
                </div>
              </div>
            </div>
          `}
    </div>
  `;
}
