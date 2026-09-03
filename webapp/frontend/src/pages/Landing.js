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
    body: "The pairs strategy runs an actual Engle-Granger test and a Kalman-filtered dynamic hedge ratio before ever sizing a trade — against a real matching engine, not a backtest replay with an invented Sharpe.",
  },
  {
    title: "Kelly-sized, risk-capped",
    body: "Fractional Kelly position sizing with a hard exposure ceiling independent of the math — no single calculation is trusted to bound itself.",
  },
  {
    title: "Options chain, real Greeks",
    body: "A synthetic index options chain priced with Black-Scholes — delta, gamma, theta, vega computed and re-marked from the live underlying, not looked up from a static table.",
  },
  {
    title: "Portfolio attribution, not a guess",
    body: "Brinson-style return attribution and a mark-to-market equity curve computed from actual fills — the same data the account panel shows, not a separate number invented for a chart.",
  },
  {
    title: "Live broker, human-gated",
    body: "Real order routing through Angel One's SmartAPI. Submitting a live order only ever stages it — a second, explicit confirmation is the sole path that ever reaches the broker.",
  },
];

const MODES = [
  {
    key: "paper",
    label: "Paper",
    body: "Fully automated, unlimited simulated capital, running against bourse's real matching engine — every strategy's default habitat.",
  },
  {
    key: "virtual",
    label: "Virtual",
    body: "A fresh ₹1,00,00,000 simulated account, same real matching engine — for sizing a strategy against a realistic starting balance before it ever sees a rupee of real capital.",
  },
  {
    key: "live",
    label: "Live",
    body: "Real orders through a connected broker. Unlocks only with a stored, complete broker credential, and every single order still needs an explicit human confirmation to reach it.",
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
      <div class="landing-glow" />
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
          Five strategies, a synthetic options chain, and real portfolio attribution, all running against bourse's
          own matching engine — not a backtest replay. Paper and virtual modes are fully automated; live mode
          routes through a real broker and always waits on a human to confirm.
        </p>
        <div style=${{ display: "flex", gap: "12px", marginTop: "28px" }}>
          <a href="#/terminal" class="btn btn-primary btn-block" style=${{ width: "auto", padding: "12px 22px" }}>Open Terminal →</a>
          <a href="#/strategies" class="btn btn-ghost" style=${{ padding: "12px 22px" }}>View Strategies</a>
        </div>
      </div>

      <div class="landing-stats">
        <div class="stat-card"><div class="stat-label">Strategies</div><div class="stat-value mono">${strategyCount ?? "—"}</div></div>
        <div class="stat-card"><div class="stat-label">Submit / Cancel / Sweep (p50)</div><div class="stat-value mono">80 / 19 / 123ns</div></div>
        <div class="stat-card"><div class="stat-label">Sustained Throughput</div><div class="stat-value mono">4.54M ops/sec</div><div class="stat-sub">at 2,255,203 resting orders</div></div>
        <div class="stat-card"><div class="stat-label">Allocations</div><div class="stat-value mono">16</div><div class="stat-sub">across 27,000,000 operations</div></div>
      </div>

      <div class="panel panel-pad" style=${{ maxWidth: "1000px", margin: "20px auto 0" }}>
        <div style=${{ fontWeight: 700, fontSize: "13px", marginBottom: "8px" }}>The honest strategy finding, not an invented Sharpe</div>
        <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6 }}>
          12 strategies backtested across seeded Monte Carlo paths. Gross directional alpha is positive; net is
          negative after 4bps round-trip costs. Fee drag is 92–103% of realized P&L, flat across horizons from
          500 to 20,000 bars. That's a stronger claim than any backtested Sharpe ratio, and it's true.
        </div>
      </div>

      <div style=${{ maxWidth: "1000px", margin: "60px auto 0", padding: "0 24px" }}>
        <div style=${{ fontWeight: 700, fontSize: "16px", marginBottom: "4px" }}>Three modes, one platform</div>
        <div style=${{ color: "var(--text-faint)", fontSize: "12.5px", marginBottom: "18px" }}>
          The same strategies, the same matching engine, the same account panel — only the capital and the broker
          connection change.
        </div>
        <div class="landing-modes">
          ${MODES.map((m) => html`
            <div key=${m.key} class="panel panel-pad">
              <span class=${`badge badge-mode-${m.key}`} style=${{ marginBottom: "10px", display: "inline-block" }}>${m.label.toUpperCase()} MODE</span>
              <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6 }}>${m.body}</div>
            </div>
          `)}
        </div>
      </div>

      <div class="landing-features">
        ${FEATURES.map((f) => html`
          <div key=${f.title} class="panel panel-pad">
            <div style=${{ fontWeight: 700, fontSize: "14px", marginBottom: "8px" }}>${f.title}</div>
            <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6 }}>${f.body}</div>
          </div>
        `)}
      </div>

      <div style=${{ textAlign: "center", padding: "60px 20px 70px" }}>
        <div style=${{ fontWeight: 700, fontSize: "18px", marginBottom: "10px" }}>See it running, not just described</div>
        <div style=${{ color: "var(--text-dim)", fontSize: "13px", marginBottom: "22px" }}>
          Paper and virtual modes need no setup — every strategy is already trading against the live matching engine.
        </div>
        <a href="#/terminal" class="btn btn-primary" style=${{ padding: "12px 26px" }}>Open Terminal →</a>
        <div style=${{ color: "var(--text-faint)", fontSize: "11.5px", marginTop: "22px" }}>
          Live mode requires a connected broker credential, and every live order is explicitly confirmed — never automatic.
        </div>
      </div>
    </div>
  `;
}
