import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, fmtPct, pnlClass } from "../format.js";
import { Calendar } from "../components/Calendar.js";
import { Sparkline } from "../components/Sparkline.js";
import { Gauge } from "../components/Gauge.js";
import { useToast } from "../toast.js";

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

function NotesPanel() {
  const [notes, setNotes] = React.useState([]);
  const [text, setText] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const toast = useToast();

  const load = React.useCallback(() => {
    api.dashboard.notes.list().then(setNotes).finally(() => setLoading(false));
  }, []);
  React.useEffect(load, [load]);

  async function add() {
    if (!text.trim()) return;
    try {
      await api.dashboard.notes.create(text.trim());
      setText("");
      load();
    } catch (e) {
      toast(e.message || "Could not save note", "err");
    }
  }

  async function remove(id) {
    await api.dashboard.notes.delete(id);
    load();
  }

  return html`
    <div class="panel panel-pad">
      <div class="panel-title">Journal Notes</div>
      <div style=${{ display: "flex", gap: "8px", marginBottom: "14px" }}>
        <input class="input" placeholder="What happened today?" value=${text}
               onInput=${(e) => setText(e.target.value)}
               onKeyDown=${(e) => e.key === "Enter" && add()} />
        <button class="btn btn-primary" onClick=${add}>Add</button>
      </div>
      ${loading && html`<div class="skeleton" style=${{ height: "60px" }} />`}
      ${!loading && !notes.length && html`<div style=${{ color: "var(--text-faint)" }}>No notes yet — jot down why you made a trade, or how the session went.</div>`}
      ${!loading && notes.map((n) => html`
        <div key=${n.id} class="row hairline" style=${{ alignItems: "flex-start" }}>
          <div>
            <div style=${{ color: "var(--text-faint)", fontSize: "10.5px", marginBottom: "3px" }}>${new Date(n.created_at).toLocaleString()}</div>
            <div>${n.text}</div>
          </div>
          <button class="btn btn-sm btn-ghost" onClick=${() => remove(n.id)}>✕</button>
        </div>
      `)}
    </div>
  `;
}

export function Dashboard() {
  const [stats, setStats] = React.useState(null);
  const [calendar, setCalendar] = React.useState([]);

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

      <div style=${{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "12px", marginBottom: "18px" }} class="dash-stats">
        <${StatCard} label="Net P&L" value=${fmtMoney(stats?.net_pnl)} valueClass=${pnlClass(stats?.net_pnl)}
          right=${html`<${Sparkline} values=${equityCurve} />`} />
        <${StatCard} label="Trade Win Rate" value=${fmtPct(stats?.win_rate)} sub=${stats ? `${stats.n_trades} trades` : ""} />
        <div class="stat-card" style=${{ display: "flex", flexDirection: "column" }}>
          <div class="stat-label">Day Win Rate</div>
          <div class=${`stat-value mono`}>${fmtPct(stats?.day_win_rate)}</div>
          <${DayWinBars} calendar=${calendar} />
        </div>
        <div class="stat-card" style=${{ display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div class="stat-label" style=${{ alignSelf: "flex-start" }}>Profit Factor</div>
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
