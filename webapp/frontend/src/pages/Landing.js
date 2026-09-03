import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { OrderBook } from "../components/OrderBook.js";
import { fmtMoney } from "../format.js";

// The Go engine's own real scaling benchmark (bench/throughput.go,
// results/throughput.json's "scaling" array) -- transcribed once, same
// as this page's other headline numbers, and cross-checked against the
// file at commit time. NOT re-fetched at runtime: this is an offline
// artifact regenerated only via BOURSE_REGEN=1 (see the repo root
// README's own "How correctness is verified" section), not something
// that changes between page loads. 4 REAL measured points, never
// interpolated for a depth in between -- a slider computing "new"
// numbers for an untested depth would be exactly the kind of fabricated
// figure this whole project has avoided from its very first spec.
const THROUGHPUT_SCALING = [
  { liveOrders: 74391, opsPerSec: 6939552, nsPerOp: 144.1 },
  { liveOrders: 375193, opsPerSec: 5186322, nsPerOp: 192.8 },
  { liveOrders: 746792, opsPerSec: 4606356, nsPerOp: 217.1 },
  { liveOrders: 2255203, opsPerSec: 4539609, nsPerOp: 220.3 },
];
// results/latency.json -- ONE flat measurement (2M ops), not per-depth
// the way throughput is -- shown as exactly that, not stretched into a
// second 4-way toggle it has no real data for.
const LATENCY_PERCENTILES = {
  submit: { p50: 80, p90: 95, p99: 119 },
  cancel: { p50: 19, p90: 23, p99: 27 },
  sweep: { p50: 123, p90: 170, p99: 225 },
};

function fmtOps(n) {
  return n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : `${(n / 1e3).toFixed(0)}K`;
}

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

function BenchmarkExplorer() {
  const [i, setI] = React.useState(THROUGHPUT_SCALING.length - 1);
  const row = THROUGHPUT_SCALING[i];

  return html`
    <div class="glass-panel panel-pad">
      <div style=${{ fontWeight: 700, fontSize: "14px", marginBottom: "4px" }}>Throughput at real, measured book depths</div>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "16px" }}>
        4 actual runs (bench/throughput.go) — not interpolated. Cost per operation rises as resting orders grow;
        the headline 4.54M ops/sec above is the largest run, the most conservative figure to quote.
      </div>
      <div style=${{ display: "flex", gap: "8px", marginBottom: "18px", flexWrap: "wrap" }}>
        ${THROUGHPUT_SCALING.map((r, idx) => html`
          <button
            key=${r.liveOrders}
            class=${`btn btn-sm ${idx === i ? "btn-primary" : "btn-ghost"}`}
            onClick=${() => setI(idx)}
          >${r.liveOrders.toLocaleString()} orders</button>
        `)}
      </div>
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px" }}>
        <div>
          <div class="stat-label">Throughput</div>
          <div class="stat-value mono">${fmtOps(row.opsPerSec)} ops/sec</div>
        </div>
        <div>
          <div class="stat-label">Cost per op</div>
          <div class="stat-value mono">${row.nsPerOp.toFixed(1)}ns</div>
        </div>
        <div>
          <div class="stat-label">Live orders</div>
          <div class="stat-value mono">${row.liveOrders.toLocaleString()}</div>
        </div>
      </div>

      <div style=${{ borderTop: "1px solid var(--border)", margin: "18px 0 14px" }} />

      <div style=${{ fontWeight: 700, fontSize: "13px", marginBottom: "4px" }}>Latency, one real 2M-operation run</div>
      <div style=${{ color: "var(--text-faint)", fontSize: "11.5px", marginBottom: "12px" }}>
        results/latency.json — a single flat measurement, not broken out per depth the way throughput is above.
      </div>
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px" }}>
        ${Object.entries(LATENCY_PERCENTILES).map(([op, pcts]) => html`
          <div key=${op}>
            <div class="stat-label">${op}</div>
            <div class="mono" style=${{ fontSize: "13px" }}>
              <div>p50 <b>${pcts.p50}ns</b></div>
              <div style=${{ color: "var(--text-faint)" }}>p90 ${pcts.p90}ns</div>
              <div style=${{ color: "var(--text-faint)" }}>p99 ${pcts.p99}ns</div>
            </div>
          </div>
        `)}
      </div>
    </div>
  `;
}

const LANDING_BOOK_SYMBOL = "ICICIBANK";

function useLandingBook() {
  const [tick, setTick] = React.useState(null);
  const [status, setStatus] = React.useState("connecting");
  React.useEffect(() => {
    const unsub = subscribeMarket(LANDING_BOOK_SYMBOL, setTick, setStatus);
    return unsub;
  }, []);
  return { tick, status };
}

function LiveBookPreview() {
  const { tick, status } = useLandingBook();
  return html`
    <div class="glass-panel panel-pad">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
        <div style=${{ fontWeight: 700, fontSize: "14px" }}>${LANDING_BOOK_SYMBOL} — real, live order book</div>
        <span class=${`badge ${status === "live" ? "badge-live" : "badge-off"}`}>
          ${status === "live" ? "● Live" : status === "connecting" ? "Connecting…" : "Reconnecting…"}
        </span>
      </div>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "14px" }}>
        Streaming from the same matching engine every real order in this app trades against — read-only here,
        no click-to-trade (this is a shared demo engine, not a private sandbox per visitor).
      </div>
      <${OrderBook} tick=${tick} stale=${status !== "live"} />
    </div>
  `;
}

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
        <div style=${{ fontWeight: 700, fontSize: "16px", marginBottom: "4px" }}>See it, don't just take the numbers above on faith</div>
        <div style=${{ color: "var(--text-faint)", fontSize: "12.5px", marginBottom: "18px" }}>
          A real, currently-streaming order book, and every real benchmark configuration this project has actually measured.
        </div>
        <div class="landing-proof">
          <${LiveBookPreview} />
          <${BenchmarkExplorer} />
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
