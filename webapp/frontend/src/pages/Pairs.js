import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, fmtNum, pnlClass } from "../format.js";
import { LineChart } from "../components/LineChart.js";
import { useToast } from "../toast.js";

const TABS = ["Overview", "Analytics"];

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
              <div style=${{ display: "flex", gap: "24px", marginBottom: "14px" }}>
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

      ${!data
        ? html`<div class="skeleton" style=${{ height: "220px" }} />`
        : tab === "Overview"
          ? html`<${OverviewTab} data=${data} />`
          : html`<${AnalyticsTab} data=${data} onForceClose=${forceClose} closing=${closing} />`}
    </div>
  `;
}
