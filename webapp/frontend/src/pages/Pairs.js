import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, fmtNum, pnlClass } from "../format.js";
import { LineChart } from "../components/LineChart.js";
import { BellCurve } from "../components/BellCurve.js";
import { SymbolSearch } from "../components/SymbolSearch.js";
import { useLiveEquityNames } from "../components/LiveSymbolSearch.js";
import { LiveOrderConfirmModal } from "../components/LiveOrderConfirmModal.js";
import { useToast } from "../toast.js";
import { useMode } from "../mode.js";

const TABS = ["Overview", "Analytics", "Custom Pair"];

function StatCard({ label, value, valueClass = "", sub }) {
  return html`
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class=${`stat-value mono ${valueClass}`}>${value}</div>
      ${sub && html`<div class="stat-sub">${sub}</div>`}
    </div>
  `;
}

function CointBadge({ isCointegrated }) {
  if (isCointegrated === null || isCointegrated === undefined) return null;
  return isCointegrated
    ? html`<span class="badge badge-live">COINTEGRATED</span>`
    : html`<span class="badge badge-off">NOT COINTEGRATED</span>`;
}

function PositionBadge({ position }) {
  const label = position === "long_spread" ? "LONG SPREAD" : position === "short_spread" ? "SHORT SPREAD" : "FLAT";
  const cls = position === "long_spread" ? "badge-live" : position === "short_spread" ? "badge-live" : "badge-off";
  return html`<span class=${`badge ${cls}`}>${label}</span>`;
}

function LegsTable({ legs, symbolA, symbolB }) {
  const rows = [symbolA, symbolB].map((s) => legs[s]).filter(Boolean);
  if (!rows.length) {
    return html`<div style=${{ color: "var(--text-faint)", padding: "12px 0" }}>No open legs</div>`;
  }
  return html`
    <div>
      <div class="table-row hairline" style=${{ color: "var(--text-faint)", fontSize: "10.5px", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        <span>Leg</span><span>Qty</span><span>Avg Entry</span><span>Unrealized</span>
      </div>
      ${rows.map((p) => html`
        <div key=${p.symbol} class="table-row hairline">
          <span style=${{ fontWeight: 600 }}>${p.symbol}</span>
          <span class=${`mono ${pnlClass(p.qty)}`}>${p.qty > 0 ? "+" : ""}${fmtNum(p.qty)}</span>
          <span class="mono">${fmtMoney(p.avg_entry_px)}</span>
          <span class=${`mono ${pnlClass(p.unrealized_pnl)}`}>${fmtMoney(p.unrealized_pnl)}</span>
        </div>
      `)}
    </div>
  `;
}

function OverviewTab({ data }) {
  if (data.warming_up) {
    return html`
      <div class="panel panel-pad" style=${{ textAlign: "center", color: "var(--text-faint)", padding: "40px" }}>
        Building up enough price history on ${data.symbol_a}/${data.symbol_b} before the Kalman filter and
        cointegration test have anything to compute — this fills in within the first minute or two of the market running.
      </div>
    `;
  }

  return html`
    <${React.Fragment}>
      <div style=${{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "14px" }}>
        <${CointBadge} isCointegrated=${data.is_cointegrated} />
        <${PositionBadge} position=${data.position} />
      </div>

      <div style=${{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px", marginBottom: "18px" }} class="dash-stats">
        <${StatCard} label="Spread Z-Score" value=${data.zscore?.toFixed(3) ?? "—"}
          sub=${`entry ±${data.config.entry_z}σ · stop ±${data.config.stop_z}σ`} />
        <${StatCard} label="Kalman Hedge Ratio (β)" value=${data.hedge_ratio?.toFixed(4) ?? "—"}
          sub=${`${data.symbol_a} : ${data.symbol_b} leg sizing`} />
        <${StatCard} label="Cointegration p-value" value=${data.cointegration_pvalue?.toFixed(4) ?? "—"}
          sub=${`ceiling ${data.config.coint_pvalue_max}`} />
        <${StatCard} label="Correlation" value=${data.correlation?.toFixed(3) ?? "—"} />
      </div>

      <div class="panel panel-pad" style=${{ marginBottom: "14px" }}>
        <div class="panel-title">Spread Position (z-gauge)</div>
        <${BellCurve} zScore=${data.zscore ?? null}
                      entryZ=${data.zscore !== null && data.zscore < 0 ? -data.config.entry_z : data.config.entry_z}
                      stopZ=${data.zscore !== null && data.zscore < 0 ? -data.config.stop_z : data.config.stop_z}
                      height=${160} />
      </div>

      <div class="dash-grid">
        <div class="panel panel-pad">
          <div class="panel-title">Strategy Parameters</div>
          <div class="row hairline"><span>Entry Z-Score</span><span class="mono">±${data.config.entry_z}σ</span></div>
          <div class="row hairline"><span>Exit Z-Score</span><span class="mono">${data.config.exit_z}σ</span></div>
          <div class="row hairline"><span>Stop Z-Score</span><span class="mono">±${data.config.stop_z}σ</span></div>
          <div class="row hairline"><span>Cointegration p-value max</span><span class="mono">${data.config.coint_pvalue_max}</span></div>
          <div class="row hairline"><span>Z-Score Window</span><span class="mono">${data.config.zscore_window} ticks</span></div>
          <div class="row hairline"><span>Min History</span><span class="mono">${data.config.min_history} ticks</span></div>
          <div class="row"><span>Base Leg Qty</span><span class="mono">${data.config.qty}</span></div>
          <div style=${{ color: "var(--text-faint)", fontSize: "11px", marginTop: "10px", lineHeight: 1.6 }}>
            Read-only — these are the exact thresholds the live strategy runs with, not a settings UI that could drift out of sync with them.
          </div>
        </div>

        <div class="panel panel-pad">
          <div class="panel-title">Signal Intelligence</div>
          ${!data.activity.length
            ? html`<div style=${{ color: "var(--text-faint)", padding: "12px 0" }}>No signals yet on this pair</div>`
            : data.activity.map((o) => html`
              <div key=${o.id} class="row hairline" style=${{ alignItems: "flex-start" }}>
                <div>
                  <span class=${o.side === "buy" ? "pos" : "neg"} style=${{ fontWeight: 600 }}>${o.side === "buy" ? "▲ BUY" : "▼ SELL"} ${o.symbol}</span>
                  <div style=${{ color: "var(--text-faint)", fontSize: "10.5px", marginTop: "3px" }}>
                    ${new Date(o.created_at).toLocaleTimeString()}
                    ${o.entry_zscore !== null && ` · entry z=${o.entry_zscore.toFixed(2)}`}
                  </div>
                </div>
                <span class="mono">${o.filled_qty}/${o.qty}</span>
              </div>
            `)}
        </div>
      </div>
    <//>
  `;
}

function AnalyticsTab({ data, onForceClose, closing }) {
  if (data.warming_up) {
    return html`
      <div class="panel panel-pad" style=${{ textAlign: "center", color: "var(--text-faint)", padding: "40px" }}>
        Not enough price history yet to chart the spread — check back once the market's been running a little longer.
      </div>
    `;
  }

  const bands = [
    { value: data.entry_z, color: "var(--accent)", label: `entry +${data.entry_z}` },
    { value: -data.entry_z, color: "var(--accent)", label: `entry −${data.entry_z}` },
    { value: data.stop_z, color: "var(--ask-bright)", label: `stop +${data.stop_z}` },
    { value: -data.stop_z, color: "var(--ask-bright)", label: `stop −${data.stop_z}` },
    { value: data.exit_z, color: "var(--text-faint)", label: `exit ${data.exit_z}` },
  ];

  const totalUnrealized = Object.values(data.legs).reduce((s, l) => s + l.unrealized_pnl, 0);

  return html`
    <${React.Fragment}>
      <div class="charts-grid" style=${{ marginBottom: "14px" }}>
        <div class="panel panel-pad">
          <div class="panel-title">Spread Z-Score</div>
          <${LineChart} series=${data.zscore_series} bands=${bands} color="var(--accent-bright)" />
        </div>
        <div class="panel panel-pad">
          <div class="panel-title">Kalman Hedge Ratio (β)</div>
          <${LineChart} series=${data.hedge_ratio_series} color="var(--violet)" />
        </div>
        <div class="panel panel-pad">
          <div class="panel-title">Raw Spread</div>
          <${LineChart} series=${data.spread_series} color="var(--accent-bright)" />
        </div>
      </div>

      <div style=${{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px", marginBottom: "14px" }} class="dash-stats">
        <${StatCard} label="Correlation" value=${data.correlation?.toFixed(3) ?? "—"} />
        <${StatCard} label="Cointegration p-value" value=${data.cointegration_pvalue?.toFixed(4) ?? "—"}
          sub=${data.is_cointegrated ? "cointegrated" : "not cointegrated"} valueClass=${data.is_cointegrated ? "pos" : "neg"} />
        <${StatCard} label="Hedge Ratio (β)" value=${data.hedge_ratio?.toFixed(4) ?? "—"} />
        <${StatCard} label="Position" value=${data.position === "none" ? "Flat" : data.position === "long_spread" ? "Long" : "Short"} />
      </div>

      <div class="panel panel-pad">
        <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
          <div class="panel-title" style=${{ margin: 0 }}>Open Position</div>
          ${data.position !== "none" && html`
            <button class="btn btn-sm btn-sell" disabled=${closing} onClick=${onForceClose}>
              ${closing ? "Closing…" : "Force Close"}
            </button>
          `}
        </div>
        ${data.position === "none"
          ? html`<div style=${{ color: "var(--text-faint)", padding: "8px 0" }}>No open spread position</div>`
          : html`
            <${React.Fragment}>
              <div style=${{ display: "flex", flexWrap: "wrap", gap: "16px 24px", marginBottom: "14px" }}>
                <div>
                  <div class="stat-label">Entry Z-Score</div>
                  <div class="mono" style=${{ fontWeight: 600 }}>${data.entry_zscore !== null ? data.entry_zscore.toFixed(3) : "—"}</div>
                </div>
                <div>
                  <div class="stat-label">Combined Unrealized P&L</div>
                  <div class=${`mono ${pnlClass(totalUnrealized)}`} style=${{ fontWeight: 600 }}>${fmtMoney(totalUnrealized)}</div>
                </div>
              </div>
              <${LegsTable} legs=${data.legs} symbolA=${data.symbol_a} symbolB=${data.symbol_b} />
            <//>
          `}
      </div>
    <//>
  `;
}

// Real cointegration testing for ANY pair, not just the validated
// ICICIBANK/HDFCBANK one -- see app/routers/pairs.py's test_custom_pair
// docstring on why most pairs a user tries here genuinely won't be
// cointegrated, and why that's reported honestly rather than softened.
// "Take Trade" reuses the EXACT same two-independent-requests-plus-
// batched-live-confirmation pattern ManualTrade.js's own SpreadTicket
// uses for the validated pair -- a custom pair gets no less safety than
// the validated one does, just no strategy_key tag (this is a manual
// trade, not strategy-generated activity, so it must not show up
// attributed to pairs_cointegration in Dashboard/Pairs Overview stats).
function CustomPairTab() {
  const tradingMode = useMode();
  const isLive = tradingMode === "live";
  const [symbolA, setSymbolA] = React.useState(null);
  const [symbolB, setSymbolB] = React.useState(null);
  const [qtyA, setQtyA] = React.useState("10");
  const [stats, setStats] = React.useState(null);
  const [testing, setTesting] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [pendingLiveOrders, setPendingLiveOrders] = React.useState(null);
  const toast = useToast();

  const liveNames = useLiveEquityNames();
  const [localNames, setLocalNames] = React.useState([]);
  React.useEffect(() => {
    if (tradingMode !== "live") {
      api.symbols().then((rows) => setLocalNames(rows.filter((s) => !s.is_derived).map((s) => s.symbol)));
    }
  }, [tradingMode]);
  const searchNames = tradingMode === "live" ? liveNames : localNames;

  async function runTest() {
    if (!symbolA || !symbolB) return toast("Pick both symbols first", "err");
    if (symbolA === symbolB) return toast("Pick two different symbols", "err");
    const qty = parseInt(qtyA, 10);
    if (!qty || qty <= 0) return toast("Enter a valid quantity", "err");

    setTesting(true);
    setStats(null);
    try {
      const result = await api.pairs.test(symbolA, symbolB, tradingMode, qty);
      setStats(result);
    } catch (e) {
      toast(e.message || "Could not test this pair", "err");
    } finally {
      setTesting(false);
    }
  }

  async function takeTrade() {
    if (!stats || stats.suggested_direction === "none") return;
    const sideA = stats.suggested_direction === "long_spread" ? "buy" : "sell";
    const sideB = stats.suggested_direction === "long_spread" ? "sell" : "buy";

    setSubmitting(true);
    let orderA = null;
    try {
      // Two independent requests, not one atomic call -- same reasoning
      // as SpreadTicket's own submit(): if leg B's request itself fails,
      // leg A may already be live, and the catch below has to say so
      // rather than hide a real one-sided position behind a generic error.
      orderA = await api.orders.submit({
        symbol: stats.symbol_a, side: sideA, order_type: "market", qty: stats.suggested_qty_a, mode: tradingMode,
      });
      const orderB = await api.orders.submit({
        symbol: stats.symbol_b, side: sideB, order_type: "market", qty: stats.suggested_qty_b, mode: tradingMode,
      });
      const pending = [orderA, orderB].filter((o) => o.status === "pending_confirmation");
      if (pending.length) {
        setPendingLiveOrders(pending);
      } else {
        toast(`Custom pair submitted — ${stats.symbol_a} ${orderA.filled_qty}/${stats.suggested_qty_a}, ${stats.symbol_b} ${orderB.filled_qty}/${stats.suggested_qty_b}`, "ok");
      }
    } catch (e) {
      if (orderA && orderA.status === "pending_confirmation") {
        setPendingLiveOrders([orderA]);
        toast(`${stats.symbol_b} leg failed before ever submitting: ${e.message || "order rejected"} — ${stats.symbol_a} leg is pending confirmation, review it below before deciding`, "err");
      } else if (orderA) {
        toast(`${stats.symbol_a} leg went through (${orderA.filled_qty}/${stats.suggested_qty_a} filled) but ${stats.symbol_b} leg failed: ${e.message || "order rejected"} — check your positions for a one-sided fill and close it manually if needed`, "err");
      } else {
        toast(e.message || "Order rejected", "err");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return html`
    <div class="panel panel-pad" style=${{ marginBottom: "14px" }}>
      <div class="panel-title">Test Any Pair</div>
      <div style=${{ color: "var(--text-faint)", fontSize: "11.5px", marginBottom: "12px", lineHeight: 1.6 }}>
        Runs the real Engle-Granger cointegration test and Kalman hedge ratio on whatever two symbols you pick --
        the same math as the validated ICICIBANK/HDFCBANK pair above, just for a pair that hasn't been proven yet.
        Most pairs won't be cointegrated -- that's reported honestly, not hidden.
      </div>
      <div style=${{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: "10px", alignItems: "start", marginBottom: "10px" }}>
        <div>
          <label style=${{ fontSize: "10.5px", color: "var(--text-faint)" }}>Symbol A ${symbolA ? `— ${symbolA}` : ""}</label>
          <${SymbolSearch} names=${searchNames} onSelect=${setSymbolA} placeholder="Search symbol A…" />
        </div>
        <div>
          <label style=${{ fontSize: "10.5px", color: "var(--text-faint)" }}>Symbol B ${symbolB ? `— ${symbolB}` : ""}</label>
          <${SymbolSearch} names=${searchNames} onSelect=${setSymbolB} placeholder="Search symbol B…" />
        </div>
        <div>
          <label style=${{ fontSize: "10.5px", color: "var(--text-faint)" }}>Qty (leg A)</label>
          <input class="input" type="number" min="1" value=${qtyA} onInput=${(e) => setQtyA(e.target.value)} style=${{ width: "90px" }} />
        </div>
      </div>
      <button class="btn btn-primary" disabled=${testing} onClick=${runTest}>${testing ? "Testing…" : "Test Cointegration"}</button>
    </div>

    ${stats && html`
      <div class="panel panel-pad">
        <div class="panel-title">${stats.symbol_a} / ${stats.symbol_b}</div>
        ${stats.warming_up
          ? html`<div style=${{ color: "var(--text-faint)", padding: "16px 0" }}>
              Only ${stats.n_bars} bars of history for this pair so far -- not enough yet (needs real price history
              to build up first).
            </div>`
          : html`
            <${React.Fragment}>
              <div style=${{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "14px" }}>
                <${CointBadge} isCointegrated=${stats.is_cointegrated} />
              </div>
              <div style=${{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px", marginBottom: "16px" }} class="dash-stats">
                <${StatCard} label="Spread Z-Score" value=${stats.zscore?.toFixed(3) ?? "—"} />
                <${StatCard} label="Kalman Hedge Ratio (β)" value=${stats.hedge_ratio?.toFixed(4) ?? "—"} />
                <${StatCard} label="Cointegration p-value" value=${stats.cointegration_pvalue?.toFixed(4) ?? "—"} />
                <${StatCard} label="Correlation" value=${stats.correlation?.toFixed(3) ?? "—"} />
              </div>
              ${stats.suggested_direction === "none"
                ? html`<div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>
                    ${stats.is_cointegrated
                      ? "Cointegrated, but the spread isn't far enough from its mean right now for an entry signal."
                      : "Not cointegrated -- no honest basis to suggest a trade on this pair."}
                  </div>`
                : html`
                  <div style=${{ marginBottom: "12px" }}>
                    <div class="row hairline">
                      <span>${stats.suggested_direction === "long_spread" ? "Buy" : "Sell"} ${stats.symbol_a}</span>
                      <span class="mono">${stats.suggested_qty_a}</span>
                    </div>
                    <div class="row">
                      <span>${stats.suggested_direction === "long_spread" ? "Sell" : "Buy"} ${stats.symbol_b}</span>
                      <span class="mono">${stats.suggested_qty_b}</span>
                    </div>
                  </div>
                  <button class="btn btn-primary btn-block" disabled=${submitting} onClick=${takeTrade}>
                    ${submitting ? "Submitting…" : `Take Trade (${stats.suggested_direction === "long_spread" ? "Long" : "Short"} Spread)`}
                  </button>
                  ${isLive && html`<div style=${{ color: "var(--text-faint)", fontSize: "11px", marginTop: "8px" }}>Both legs are independent live orders — you'll confirm them together before either reaches the broker.</div>`}
                `}
            <//>
          `}
      </div>
    `}

    ${pendingLiveOrders && html`
      <${LiveOrderConfirmModal} orders=${pendingLiveOrders}
        onDone=${() => setPendingLiveOrders(null)}
        onClose=${() => setPendingLiveOrders(null)} />
    `}
  `;
}

export function Pairs() {
  const [tab, setTab] = React.useState("Overview");
  const [overview, setOverview] = React.useState(null);
  const [analytics, setAnalytics] = React.useState(null);
  const [closing, setClosing] = React.useState(false);
  const toast = useToast();

  const load = React.useCallback(() => {
    api.pairs.overview().then(setOverview).catch(() => {});
    api.pairs.analytics().then(setAnalytics).catch(() => {});
  }, []);

  React.useEffect(() => { load(); }, [load]);
  React.useEffect(() => {
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  async function forceClose() {
    setClosing(true);
    try {
      await api.pairs.close();
      toast("Pair position closed", "ok");
      load();
    } catch (e) {
      toast(e.message || "Could not close position", "err");
    } finally {
      setClosing(false);
    }
  }

  const data = tab === "Overview" ? overview : analytics;

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Pairs</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "14px" }}>
        ${overview ? `${overview.symbol_a} / ${overview.symbol_b}` : "Loading pair…"} — cointegration + Kalman-filtered hedge ratio, ported from Vipluv's own icici_mean_reversion backtest
      </div>

      <div class="tabs">
        ${TABS.map((t) => html`
          <div key=${t} class=${`tab ${tab === t ? "active" : ""}`} onClick=${() => setTab(t)}>${t}</div>
        `)}
      </div>

      ${tab === "Custom Pair"
        ? html`<${CustomPairTab} />`
        : !data
          ? html`<div class="skeleton" style=${{ height: "220px" }} />`
          : tab === "Overview"
            ? html`<${OverviewTab} data=${data} />`
            : html`<${AnalyticsTab} data=${data} onForceClose=${forceClose} closing=${closing} />`}
    </div>
  `;
}
