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

const MODE_LABEL = { paper: "paper mode", virtual: "virtual mode (₹1 Cr simulated capital)", live: "live mode — real Angel One trading" };

export function Dashboard() {
  const [stats, setStats] = React.useState(null);
  const [calendar, setCalendar] = React.useState([]);
  const mode = useMode();

  // Real bug fixed 2026-09-04: this used to ALWAYS fetch Mode.paper
  // regardless of the selected mode, covered only by a banner explaining
  // that -- confusing enough on its own that it needed asking about
  // directly. Now genuinely mode-aware: switching modes re-fetches and
  // shows THAT mode's own real numbers (get_cached_realizations already
  // supports any Mode, see app/accounting.py), not a fixed paper-only
  // view with a disclaimer bolted on.
  React.useEffect(() => {
    setStats(null);
    setCalendar([]);
    api.dashboard.stats(mode).then(setStats);
    api.dashboard.calendar(mode).then(setCalendar);
  }, [mode]);

  let running = 0;
  const equityCurve = calendar.map((d) => (running += d.pnl));

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Dashboard</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>Performance across every strategy and manual trade, ${MODE_LABEL[mode] ?? mode}</div>

      ${mode === "live" && html`
        <div class="notice-banner" style=${{ marginBottom: "18px" }}>
          <strong>Real trading activity:</strong> these numbers come from your actual Angel One trades, not a
          simulation -- a new live account will show sparse or empty stats here simply because there isn't much
          history yet, not because anything is broken.
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
