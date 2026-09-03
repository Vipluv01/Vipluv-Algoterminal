import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { OrderBook } from "../components/OrderBook.js";

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

// Eases the DISPLAYED number toward a real value on change -- the value
// itself never leaves THROUGHPUT_SCALING's 4 measured rows, this only
// animates the transition between two already-real numbers when a user
// clicks a different depth, same principle as the flash highlight in
// OrderBook.js (decorate a real change, never invent one).
function useCountUp(target, duration = 400) {
  const [display, setDisplay] = React.useState(target);
  const prevRef = React.useRef(target);

  React.useEffect(() => {
    const from = prevRef.current;
    const to = target;
    if (from === to) { setDisplay(to); return; }
    let raf = null;
    const start = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(from + (to - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
      else prevRef.current = to;
    }
    raf = requestAnimationFrame(tick);
    return () => { if (raf) cancelAnimationFrame(raf); prevRef.current = to; };
  }, [target, duration]);

  return display;
}

// Each tag names the actual technique behind that feature, and each link
// goes to the real screen where it runs -- clicking one is a live claim
// ("go see this yourself"), not a decorative pill. The two strategy-
// specific cards that used to live here (five strategies / cointegration)
// moved into StrategyShowcase below, in more depth and without duplicating
// the same claim twice on one page.
const FEATURES = [
  {
    title: "Kelly-sized, risk-capped",
    body: "Fractional Kelly position sizing with a hard exposure ceiling independent of the math — no single calculation is trusted to bound itself.",
    tags: ["#FractionalKelly", "#ExposureCeiling"],
    link: { hash: "#/risk", label: "View Risk" },
  },
  {
    title: "Options chain, real Greeks",
    body: "A synthetic index options chain priced with Black-Scholes — delta, gamma, theta, vega computed and re-marked from the live underlying, not looked up from a static table.",
    tags: ["#BlackScholes", "#Greeks"],
    link: { hash: "#/options", label: "View Options" },
  },
  {
    title: "Portfolio attribution, not a guess",
    body: "Brinson-style return attribution and a mark-to-market equity curve computed from actual fills — the same data the account panel shows, not a separate number invented for a chart.",
    tags: ["#BrinsonAttribution", "#MarkToMarket"],
    link: { hash: "#/portfolio-iq", label: "View Portfolio IQ" },
  },
  {
    title: "Live broker, human-gated",
    body: "Real order routing through Angel One's SmartAPI. Submitting a live order only ever stages it — a second, explicit confirmation is the sole path that ever reaches the broker.",
    tags: ["#AngelOneSmartAPI", "#HumanConfirmed"],
    link: { hash: "#/settings", label: "View Vault" },
  },
];

const MODES = [
  {
    key: "paper",
    label: "Paper",
    body: "Fully automated, unlimited simulated capital, running against bourse's real matching engine — every strategy's default habitat.",
    badges: ["Simulated", "Unlimited Capital", "Fully Automated"],
  },
  {
    key: "virtual",
    label: "Virtual",
    body: "A fresh ₹1,00,00,000 simulated account, same real matching engine — for sizing a strategy against a realistic starting balance before it ever sees a rupee of real capital.",
    badges: ["Simulated", "₹1 Cr Capital", "Fully Automated"],
  },
  {
    key: "live",
    label: "Live",
    body: "Real orders through a connected broker. Unlocks only with a stored, complete broker credential, and every single order still needs an explicit human confirmation to reach it.",
    badges: ["Real Broker", "Human-Gated", "Explicit Confirm"],
  },
];

// 5 real strategies from app/strategy_runner.py's own SINGLE_INSTRUMENT_
// STRATEGIES + the pairs strategy -- "mechanic" is transcribed from each
// strategy file's OWN docstring/decision logic (app/strategies/*.py),
// not invented. There is no real per-strategy equity-curve time series
// anywhere in this codebase (results/backtests.json only has aggregate
// stats -- win_rate, profit_factor, max_drawdown -- across seeded paths,
// no bar-by-bar series), so this deliberately shows the real DECISION
// RULE for each strategy instead of a sparkline/performance chart that
// would have to be fabricated to exist at all.
const STRATEGY_SHOWCASE = [
  {
    key: "alpha_rsi_ema",
    label: "Alpha",
    subtitle: "RSI + EMA Crossover",
    mechanic: "Enters on a bullish EMA(9)/EMA(21) crossover confirmed by RSI recovering out of oversold — both conditions required, never either alone (an EMA cross alone fires constantly in a choppy market).",
    tags: ["#RSI", "#EMA"],
  },
  {
    key: "momentum_macd",
    label: "Momentum",
    subtitle: "MACD Histogram + EMA(50) Filter",
    mechanic: "Trades a MACD histogram sign-flip only when price already sits on the right side of its EMA(50) trend filter — not a raw crossover fired in a sideways market.",
    tags: ["#MACD", "#TrendFilter"],
  },
  {
    key: "mean_reversion_bb",
    label: "Mean Reversion",
    subtitle: "Bollinger Fade + RSI Confirmation",
    mechanic: "Fades price back from a Bollinger Band extreme, confirmed by RSI — a single instrument's own rolling mean, distinct from the pairs strategy's spread below.",
    tags: ["#Bollinger", "#RSI"],
  },
  {
    key: "pairs_cointegration",
    label: "Pairs",
    subtitle: "Engle-Granger + Kalman Hedge Ratio",
    mechanic: "Runs a real Engle-Granger cointegration test and a Kalman-filtered dynamic hedge ratio before ever sizing a trade on the spread between two instruments.",
    tags: ["#EngleGranger", "#KalmanFilter"],
  },
  {
    key: "vwap_reversion",
    label: "VWAP Reversion",
    subtitle: "Volume-Weighted Deviation Fade",
    mechanic: "Fades price back toward session VWAP once its deviation exceeds 1.5x its own recent rolling standard deviation — a volume-weighted reference, not a plain moving average.",
    tags: ["#VWAP", "#VolumeWeighted"],
  },
];

// Real architecture facts, transcribed from the repo README's own "Core
// design" section (arena allocation with integer handles, three-level
// hierarchical bitmap, price-time/FIFO ordering with O(1) cancellation).
// Deliberately NOT "lock-free queues" -- the engine's own README and
// CLAUDE.md are explicit that a Book is single-threaded per instrument,
// fed from a sequenced stream, not a concurrent structure avoiding locks;
// the real reason it needs no locking is that nothing else ever touches
// it concurrently, which is a different (and simpler) property than
// "lock-free."
const ENGINE_BLUEPRINT = [
  { label: "Arena Allocator", detail: "Integer handles, not pointers — Go's GC never walks the order book." },
  { label: "Bitmap Indexing", detail: "Three-level hierarchical bitmap — O(1) best-price lookup, dense book or thin." },
  { label: "Price-Time Priority", detail: "FIFO within every level, O(1) cancellation via an intrusive doubly linked list." },
];

// Bar height proportional to that row's own real ops/sec (max-scaled
// against the fastest real run) -- and each bar IS BenchmarkExplorer's
// depth selector, not a decorative chart sitting next to the real
// control.
function ThroughputBarChart({ data, activeIndex, onSelect }) {
  const max = Math.max(...data.map((d) => d.opsPerSec));
  return html`
    <div style=${{ display: "flex", gap: "10px", marginBottom: "8px" }}>
      ${data.map((r, idx) => html`
        <div key=${r.liveOrders} class="tp-bar-col" style=${{ flex: 1, cursor: "pointer" }} onClick=${() => onSelect(idx)}>
          <div class="tp-bar-track">
            <div class=${`tp-bar ${idx === activeIndex ? "active" : ""}`}
                 style=${{ height: `${Math.max(6, (r.opsPerSec / max) * 100)}%` }} />
          </div>
          <div class="mono" style=${{ fontSize: "9.5px", textAlign: "center", marginTop: "6px", color: idx === activeIndex ? "var(--accent-bright)" : "var(--text-faint)" }}>
            ${r.liveOrders.toLocaleString()}
          </div>
        </div>
      `)}
    </div>
  `;
}

// Bar width proportional to that op's own real p50 (max-scaled against
// the slowest real op, sweep) -- submit and cancel both read visibly
// shorter than sweep, honestly, rather than a blanket "sub-Xns" headline
// that would be false for whichever op is slowest.
function LatencyRadar({ percentiles }) {
  const max = Math.max(...Object.values(percentiles).map((p) => p.p50));
  return html`
    <div style=${{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "16px" }}>
      ${Object.entries(percentiles).map(([op, p]) => html`
        <div key=${op}>
          <div style=${{ display: "flex", justifyContent: "space-between", fontSize: "11px", marginBottom: "4px" }}>
            <span style=${{ textTransform: "capitalize" }}>${op}</span>
            <span class="mono">${p.p50}ns p50</span>
          </div>
          <div class="latency-bar-track">
            <div class="latency-bar-fill" style=${{ width: `${Math.max(4, (p.p50 / max) * 100)}%` }} />
          </div>
        </div>
      `)}
    </div>
  `;
}

function BenchmarkExplorer() {
  const [i, setI] = React.useState(THROUGHPUT_SCALING.length - 1);
  const row = THROUGHPUT_SCALING[i];
  const animOps = useCountUp(row.opsPerSec);
  const animNs = useCountUp(row.nsPerOp);
  const animOrders = useCountUp(row.liveOrders);

  return html`
    <div class="glass-panel panel-pad">
      <div style=${{ fontWeight: 700, fontSize: "14px", marginBottom: "4px" }}>Throughput at real, measured book depths</div>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "16px" }}>
        4 actual runs (bench/throughput.go) — not interpolated. Cost per operation rises as resting orders grow;
        the headline 4.54M ops/sec above is the largest run, the most conservative figure to quote. Click a bar.
      </div>
      <${ThroughputBarChart} data=${THROUGHPUT_SCALING} activeIndex=${i} onSelect=${setI} />
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px", marginTop: "6px" }}>
        <div>
          <div class="stat-label">Throughput</div>
          <div class="stat-value mono">${fmtOps(animOps)} ops/sec</div>
        </div>
        <div>
          <div class="stat-label">Cost per op</div>
          <div class="stat-value mono">${animNs.toFixed(1)}ns</div>
        </div>
        <div>
          <div class="stat-label">Live orders</div>
          <div class="stat-value mono">${Math.round(animOrders).toLocaleString()}</div>
        </div>
      </div>

      <div style=${{ borderTop: "1px solid var(--border)", margin: "18px 0 14px" }} />

      <div style=${{ fontWeight: 700, fontSize: "13px", marginBottom: "4px" }}>Latency, one real 2M-operation run</div>
      <div style=${{ color: "var(--text-faint)", fontSize: "11.5px", marginBottom: "12px" }}>
        results/latency.json — a single flat measurement, not broken out per depth the way throughput is above.
      </div>
      <${LatencyRadar} percentiles=${LATENCY_PERCENTILES} />
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

function EngineBlueprint() {
  return html`
    <div class="glass-panel panel-pad">
      <div style=${{ fontWeight: 700, fontSize: "14px", marginBottom: "4px" }}>Engine blueprint, not a generic feature list</div>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "14px" }}>
        The real design decisions behind the numbers alongside — see the repo README's own "Core design" section.
      </div>
      ${ENGINE_BLUEPRINT.map((row) => html`
        <div key=${row.label} class="blueprint-row">
          <span class="blueprint-label">${row.label}</span>
          <span class="blueprint-detail">${row.detail}</span>
        </div>
      `)}
      <div style=${{ borderTop: "1px solid var(--border)", margin: "16px 0 14px" }} />
      <div style=${{ fontWeight: 700, fontSize: "13px", marginBottom: "2px" }}>16 allocations across 27,000,000 operations</div>
      <div class="mono" style=${{ color: "var(--accent-bright)", fontSize: "12.5px" }}>4.41 bytes/op amortized</div>
    </div>
  `;
}

function StrategyShowcase() {
  const [active, setActive] = React.useState(0);
  const s = STRATEGY_SHOWCASE[active];
  return html`
    <div class="glass-panel panel-pad">
      <div class="tabs">
        ${STRATEGY_SHOWCASE.map((row, idx) => html`
          <div key=${row.key} class=${`tab glow-hover ${idx === active ? "active" : ""}`} onClick=${() => setActive(idx)}>
            ${row.label}
          </div>
        `)}
      </div>
      <div style=${{ fontWeight: 700, fontSize: "14px", marginBottom: "4px" }}>${s.subtitle}</div>
      <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6, marginBottom: "14px" }}>${s.mechanic}</div>
      <div style=${{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
        ${s.tags.map((t) => html`<span key=${t} class="feature-tag">${t}</span>`)}
      </div>
    </div>
  `;
}

function FeatureCard({ f, expanded, onToggle }) {
  return html`
    <div class=${`feature-card panel panel-pad ${expanded ? "expanded" : ""}`} onClick=${onToggle}>
      <div style=${{ fontWeight: 700, fontSize: "14px", marginBottom: "8px" }}>${f.title}</div>
      <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6 }}>${f.body}</div>
      <div style=${{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "12px" }}>
        ${f.tags.map((t) => html`<span key=${t} class="feature-tag">${t}</span>`)}
      </div>
      ${expanded && html`
        <div style=${{ marginTop: "12px", paddingTop: "12px", borderTop: "1px solid var(--border)" }}>
          <a href=${f.link.hash} class="btn btn-sm btn-ghost" onClick=${(e) => e.stopPropagation()}>${f.link.label} →</a>
        </div>
      `}
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

function useSystemStats() {
  const [strategyCount, setStrategyCount] = React.useState(null);
  React.useEffect(() => {
    api.strategies.list().then((s) => setStrategyCount(s.length)).catch(() => {});
  }, []);
  return { strategyCount };
}

export function Landing() {
  const { strategyCount } = useSystemStats();
  const [expandedFeature, setExpandedFeature] = React.useState(null);
  const [activeMode, setActiveMode] = React.useState("paper");
  // Subscribed ONCE here and shared with the hero's book widget -- there
  // used to be a second, separately-subscribing preview lower on the
  // page; that duplicated a real WebSocket connection to the same shared
  // demo engine purely for page layout, so the widget moved up here
  // instead of being rendered twice.
  const { tick, status } = useLandingBook();

  return html`
    <div class="landing-page">
      <div class="landing-mesh" />
      <div class="landing-glow" />
      <nav class="topnav" style=${{ background: "transparent", backdropFilter: "none", borderBottom: "1px solid var(--border)" }}>
        <a href="#/" class="brand" style=${{ textDecoration: "none" }}>
          <span class="brand-mark">A</span><span>algoterminal</span>
        </a>
        <div style=${{ flex: 1 }} />
        <a href="#/terminal" class="btn btn-primary">Launch Terminal</a>
      </nav>

      <div class="landing-container landing-hero-grid">
        <div class="landing-hero-left">
          <span class="badge badge-accent landing-badge-glow" style=${{ marginBottom: "18px", display: "inline-block" }}>
            ⚡ 4.54M Ops/Sec Sustained · 19ns Cancel Latency (p50)
          </span>
          <h1 class="landing-title hero-gradient-text">Systematic trading,<br/>built on a <span class="grad-text">real</span> matching engine.</h1>
          <p class="landing-sub-left">
            Five strategies, a synthetic options chain, and real portfolio attribution, all running against bourse's
            own matching engine — not a backtest replay. Paper and virtual modes are fully automated; live mode
            routes through a real broker and always waits on a human to confirm.
          </p>
          <div style=${{ display: "flex", gap: "12px", marginTop: "28px" }}>
            <a href="#/terminal" class="btn btn-primary glow-hover" style=${{ padding: "12px 22px" }}>Launch Terminal →</a>
            <a href="#/strategies" class="btn btn-ghost" style=${{ padding: "12px 22px" }}>Explore Strategies</a>
          </div>
        </div>
        <div class="landing-hero-right">
          <div class="glass-panel panel-pad hero-book-widget">
            <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
              <div style=${{ fontWeight: 700, fontSize: "14px" }}>${LANDING_BOOK_SYMBOL} — real, live order book</div>
              <span class=${`badge ${status === "live" ? "badge-live" : "badge-off"}`}>
                ${status === "live" ? "● Live" : status === "connecting" ? "Connecting…" : "Reconnecting…"}
              </span>
            </div>
            <div style=${{ color: "var(--text-faint)", fontSize: "11.5px", marginBottom: "12px" }}>
              Streaming from the same matching engine every real order in this app trades against — read-only here,
              no click-to-trade (this is a shared demo engine, not a private sandbox per visitor).
            </div>
            <${OrderBook} tick=${tick} stale=${status !== "live"} />
          </div>
        </div>
      </div>

      <div class="landing-container landing-section">
        <div class="landing-stats glass-panel">
          <div class="stat-card"><div class="stat-label">Strategies</div><div class="stat-value mono">${strategyCount ?? "—"}</div></div>
          <div class="stat-card"><div class="stat-label">Submit / Cancel / Sweep (p50)</div><div class="stat-value mono">80 / 19 / 123ns</div></div>
          <div class="stat-card"><div class="stat-label">Sustained Throughput</div><div class="stat-value mono">4.54M ops/sec</div><div class="stat-sub">at 2,255,203 resting orders</div></div>
          <div class="stat-card"><div class="stat-label">Allocations</div><div class="stat-value mono">16</div><div class="stat-sub">across 27,000,000 operations</div></div>
        </div>
      </div>

      <div class="landing-container landing-section">
        <div class="panel panel-pad" style=${{ maxWidth: "800px", margin: "0 auto" }}>
          <div style=${{ fontWeight: 700, fontSize: "13px", marginBottom: "8px" }}>The honest strategy finding, not an invented Sharpe</div>
          <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6 }}>
            12 strategies backtested across seeded Monte Carlo paths. Gross directional alpha is positive; net is
            negative after 4bps round-trip costs. Fee drag is 92–103% of realized P&L, flat across horizons from
            500 to 20,000 bars. That's a stronger claim than any backtested Sharpe ratio, and it's true.
          </div>
        </div>
      </div>

      <div class="landing-container landing-section">
        <div style=${{ fontWeight: 700, fontSize: "16px", marginBottom: "4px" }}>Five real strategies, one engine</div>
        <div style=${{ color: "var(--text-faint)", fontSize: "12.5px", marginBottom: "18px" }}>
          Each tab is the strategy's actual decision rule (from its own source file), not a performance chart —
          there's no real per-strategy equity-curve data in this codebase to plot one from honestly.
        </div>
        <${StrategyShowcase} />
      </div>

      <div class="landing-container landing-section">
        <div style=${{ fontWeight: 700, fontSize: "16px", marginBottom: "4px" }}>See it, don't just take the numbers above on faith</div>
        <div style=${{ color: "var(--text-faint)", fontSize: "12.5px", marginBottom: "18px" }}>
          Every real benchmark configuration this project has actually measured — and the engine internals behind them.
        </div>
        <div class="landing-proof">
          <${BenchmarkExplorer} />
          <${EngineBlueprint} />
        </div>
      </div>

      <div class="landing-container landing-section">
        <div style=${{ fontWeight: 700, fontSize: "16px", marginBottom: "4px" }}>Three modes, one platform</div>
        <div style=${{ color: "var(--text-faint)", fontSize: "12.5px", marginBottom: "18px" }}>
          The same strategies, the same matching engine, the same account panel — only the capital and the broker
          connection change.
        </div>
        <div class="landing-modes">
          ${MODES.map((m) => html`
            <div key=${m.key} class=${`mode-card panel panel-pad ${activeMode === m.key ? "active" : ""}`}
                 onClick=${() => setActiveMode(m.key)}>
              <span class=${`badge badge-mode-${m.key}`} style=${{ marginBottom: "10px", display: "inline-block" }}>${m.label.toUpperCase()} MODE</span>
              <div style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6 }}>${m.body}</div>
              <div class="mode-card-badges">
                ${m.badges.map((b) => html`<span key=${b} class="mode-badge-pill">${b}</span>`)}
              </div>
            </div>
          `)}
        </div>
      </div>

      <div class="landing-container landing-section">
        <div class="landing-features">
          ${FEATURES.map((f) => html`
            <${FeatureCard} key=${f.title} f=${f}
              expanded=${expandedFeature === f.title}
              onToggle=${() => setExpandedFeature((cur) => (cur === f.title ? null : f.title))} />
          `)}
        </div>
      </div>

      <div class="landing-container landing-section" style=${{ textAlign: "center", paddingBottom: "70px" }}>
        <div style=${{ fontWeight: 700, fontSize: "18px", marginBottom: "10px" }}>See it running, not just described</div>
        <div style=${{ color: "var(--text-dim)", fontSize: "13px", marginBottom: "22px" }}>
          Paper and virtual modes need no setup — every strategy is already trading against the live matching engine.
        </div>
        <a href="#/terminal" class="btn btn-primary glow-hover" style=${{ padding: "12px 26px" }}>Open Terminal →</a>
        <div style=${{ color: "var(--text-faint)", fontSize: "11.5px", marginTop: "22px" }}>
          Live mode requires a connected broker credential, and every live order is explicitly confirmed — never automatic.
        </div>
      </div>
    </div>
  `;
}
