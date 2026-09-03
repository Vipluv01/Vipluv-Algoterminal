import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, fmtPct, pnlClass } from "../format.js";
import { Calendar } from "../components/Calendar.js";
import { Sparkline } from "../components/Sparkline.js";
import { Gauge } from "../components/Gauge.js";
import { useMode } from "../mode.js";

function StatCard({ label, value, valueClass = "", sub, right }) {
  return html`
    <div class="stat-card" style=${{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "10px" }}>
      <div>
        <div class="stat-label">${label}</div>
        <div class=${`stat-value mono ${valueClass}`}>${value}</div>
        ${sub && html`<div class="stat-sub">${sub}</div>`}
      </div>
      ${right}
    </div>
  `;
}

// One small bar per day that had a realized trade, colored by whether
// that day closed net positive -- the same "day win rate" number as a
// card up top, but shown as a shape so a losing streak is visible at a
// glance instead of only as a single aggregated percentage.
function DayWinBars({ calendar }) {
  if (!calendar.length) return null;
  const recent = calendar.slice(-20);
  return html`
    <div style=${{ display: "flex", alignItems: "flex-end", gap: "2px", height: "24px", marginTop: "8px" }}>
      ${recent.map((d) => html`
        <div key=${d.day} title=${`${d.day}: ${fmtMoney(d.pnl)}`}
             style=${{ flex: 1, height: "100%", borderRadius: "2px", background: d.pnl >= 0 ? "var(--bid-bright)" : "var(--ask-bright)", opacity: d.pnl >= 0 ? 0.9 : 0.6 }} />
      `)}
    </div>
  `;
}

// Read-only preview -- writing/deleting notes now happens exclusively on
// the Journal screen (#/journal, see app/routers/journal.py's module
// docstring: /dashboard/notes was removed, not duplicated, so there is
// exactly one place notes are read OR written). This panel just surfaces
// the 3 most recent so the Dashboard still shows journal activity at a
// glance, without re-implementing add/remove against the same table twice.
function NotesPanel() {
  const [notes, setNotes] = React.useState(null); // null = loading

  React.useEffect(() => {
    api.journal.list().then(setNotes).catch(() => setNotes([]));
  }, []);

  const recent = notes ? notes.slice(0, 3) : [];

  return html`
    <div class="panel panel-pad">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px" }}>
        <div class="panel-title" style=${{ marginBottom: 0 }}>Journal</div>
        <a href="#/journal" class="btn btn-sm btn-ghost">Open Journal →</a>
      </div>
      ${notes === null && html`<div class="skeleton" style=${{ height: "60px" }} />`}
      ${notes !== null && !notes.length && html`<div style=${{ color: "var(--text-faint)" }}>No entries yet — jot down why you made a trade, or how the session went, in the Journal.</div>`}
      ${notes !== null && recent.map((n) => html`
        <div key=${n.id} class="row hairline" style=${{ alignItems: "flex-start" }}>
          <div>
            <div style=${{ color: "var(--text-faint)", fontSize: "10.5px", marginBottom: "3px" }}>${new Date(n.created_at).toLocaleString()}</div>
            <div>${n.text}</div>
          </div>
        </div>
      `)}
    </div>
  `;
}

export function Dashboard() {
  const [stats, setStats] = React.useState(null);
  const [calendar, setCalendar] = React.useState([]);
  const mode = useMode();

  React.useEffect(() => {
    api.dashboard.stats().then(setStats);
    api.dashboard.calendar().then(setCalendar);
  }, []);

  let running = 0;
  const equityCurve = calendar.map((d) => (running += d.pnl));

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Dashboard</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>Performance across every strategy and manual trade, paper mode</div>

      ${mode !== "paper" && html`
        <div class="notice-banner" style=${{ marginBottom: "18px" }}>
          <strong>Paper mode data, always:</strong> this dashboard tracks paper-mode strategy/manual-trade
          performance only, and keeps updating from real ongoing paper activity regardless of which mode you have
          selected (currently ${mode}) -- it is not showing ${mode} activity, and there may be none. Switch to
          Paper mode to trade against what's shown here.
        </div>
      `}

      <div class="dash-stats">
        <${StatCard} label="Net P&L" value=${fmtMoney(stats?.net_pnl)} valueClass=${pnlClass(stats?.net_pnl)}
          right=${html`<${Sparkline} values=${equityCurve} />`} />
        <${StatCard} label="Trade Win Rate" value=${fmtPct(stats?.win_rate)} sub=${stats ? `${stats.n_trades} trades` : ""} />
        <div class="stat-card">
          <div class="stat-label">Day Win Rate</div>
          <div class=${`stat-value mono`}>${fmtPct(stats?.day_win_rate)}</div>
          <${DayWinBars} calendar=${calendar} />
        </div>
        <div class="stat-card">
          <div class="stat-label">Profit Factor</div>
          <${Gauge} value=${stats?.profit_factor ?? null} size=${100} />
        </div>
        <${StatCard} label="Avg Win / Loss" value=${`${fmtMoney(stats?.avg_win, { decimals: 0 })} / ${fmtMoney(stats?.avg_loss, { decimals: 0 })}`} />
      </div>

      <div class="dash-grid">
        <${NotesPanel} />
        <${Calendar} days=${calendar} />
      </div>
    </div>
  `;
}
