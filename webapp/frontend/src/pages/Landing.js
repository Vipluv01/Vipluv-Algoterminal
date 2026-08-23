import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";

const FEATURES = [
  {
    title: "Five strategies, one engine",
    body: "Alpha (RSI+EMA), Momentum (MACD), Mean Reversion (Bollinger), and a real cointegration pairs strategy — evaluated live against bourse's own Go matching engine, not a backtest replay.",
  },
  {
    title: "Real cointegration, not correlation",
    body: "The pairs strategy runs an actual Engle-Granger test and a Kalman-filtered dynamic hedge ratio before ever sizing a trade — ported from a validated methodology (Sharpe 1.74, -6.78% max drawdown, walk-forward tested).",
  },
  {
    title: "Kelly-sized, risk-capped",
    body: "Fractional Kelly position sizing with a hard exposure ceiling independent of the math — no single calculation is trusted to bound itself.",
  },
  {
    title: "Paper first, live gated",
    body: "Every strategy runs fully automated in paper mode against real simulated order flow. Live execution requires a human to confirm every order, always.",
  },
];

function useSystemStats() {
  const [strategyCount, setStrategyCount] = React.useState(null);
  React.useEffect(() => {
    api.strategies.list().then((s) => setStrategyCount(s.length)).catch(() => {});
  }, []);
  return { strategyCount };
}

export function Landing() {
  const { strategyCount } = useSystemStats();

  return html`
    <div class="landing-page">
      <nav class="topnav" style=${{ background: "transparent", backdropFilter: "none", borderBottom: "1px solid var(--border)" }}>
        <a href="#/" class="brand" style=${{ textDecoration: "none" }}>
          <span class="brand-mark">A</span><span>algoterminal</span>
        </a>
        <div style=${{ flex: 1 }} />
        <a href="#/terminal" class="btn btn-primary">Launch Terminal</a>
      </nav>

      <div class="landing-hero">
        <span class="badge badge-accent" style=${{ marginBottom: "18px" }}>● Paper Engine Live · Bourse Matching Core</span>
        <h1 class="landing-title">Systematic trading,<br/>built on a <span class="grad-text">real</span> matching engine.</h1>
        <p class="landing-sub">
          Five strategies. A cointegration pairs trade with a validated, walk-forward-tested track record.
          Kelly-sized. Paper mode fully automated, live mode always human-confirmed.
        </p>
        <div style=${{ display: "flex", gap: "12px", marginTop: "28px" }}>
          <a href="#/terminal" class="btn btn-primary btn-block" style=${{ width: "auto", padding: "12px 22px" }}>Open Terminal →</a>
          <a href="#/strategies" class="btn btn-ghost" style=${{ padding: "12px 22px" }}>View Strategies</a>
        </div>
      </div>

      <div class="landing-stats">
        <div class="stat-card"><div class="stat-label">Strategies</div><div class="stat-value mono">${strategyCount ?? "—"}</div></div>
        <div class="stat-card"><div class="stat-label">Pairs Cointegration p-value</div><div class="stat-value mono">0.024*</div><div class="stat-sub">*icici_mean_reversion backtest</div></div>
        <div class="stat-card"><div class="stat-label">Backtested Sharpe</div><div class="stat-value mono">1.74*</div></div>
        <div class="stat-card"><div class="stat-label">Max Drawdown</div><div class="stat-value mono neg">-6.78%*</div></div>
      </div>

      <div class="landing-features">
        ${FEATURES.map((f) => html`
          <div key=${f.title} class="panel panel-pad">
            <div style=${{ fontWeight: 700, fontSize: "14px", marginBottom: "8px" }}>${f.title}</div>
            <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6 }}>${f.body}</div>
          </div>
        `)}
      </div>

      <div style=${{ textAlign: "center", padding: "40px 20px 60px", color: "var(--text-faint)", fontSize: "11.5px" }}>
        Paper trading only until a broker is connected and every live order is explicitly confirmed.
      </div>
    </div>
  `;
}
