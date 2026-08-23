import React from "react";
import { html } from "../html.js";
import { fmtMoney } from "../format.js";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

// Heatmap intensity is relative to the largest |P&L| day in the dataset,
// not a fixed scale -- a portfolio that's only ever moved a few hundred
// rupees shouldn't render as uniformly pale just because a fixed scale
// was calibrated for a bigger account.
export function Calendar({ days }) {
  const [monthOffset, setMonthOffset] = React.useState(0);

  const byDay = React.useMemo(() => {
    const m = new Map();
    for (const d of days) m.set(d.day, d);
    return m;
  }, [days]);

  const maxAbs = React.useMemo(
    () => days.reduce((m, d) => Math.max(m, Math.abs(d.pnl)), 1),
    [days]
  );

  const today = new Date();
  const viewDate = new Date(today.getFullYear(), today.getMonth() + monthOffset, 1);
  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();
  const firstDow = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  const cells = [];
  for (let i = 0; i < firstDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);

  function cellStyle(d) {
    if (!d) return {};
    const key = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const info = byDay.get(key);
    if (!info) return {};
    const intensity = Math.min(1, Math.abs(info.pnl) / maxAbs);
    const color = info.pnl >= 0 ? "34,197,94" : "244,63,94";
    return { background: `rgba(${color}, ${0.1 + intensity * 0.35})`, borderColor: `rgba(${color}, ${0.3 + intensity * 0.4})` };
  }

  function cellInfo(d) {
    if (!d) return null;
    const key = `${year}-${String(month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    return byDay.get(key);
  }

  return html`
    <div class="panel panel-pad">
      <div style=${{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "14px" }}>
        <button class="btn btn-sm btn-ghost" onClick=${() => setMonthOffset((m) => m - 1)}>←</button>
        <div style=${{ fontWeight: 700, fontSize: "13px" }}>${MONTH_NAMES[month]} ${year}</div>
        <button class="btn btn-sm btn-ghost" onClick=${() => setMonthOffset((m) => m + 1)}>→</button>
      </div>
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: "4px", fontSize: "10px", color: "var(--text-faint)", marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        ${["S", "M", "T", "W", "T", "F", "S"].map((d, i) => html`<div key=${i} style=${{ textAlign: "center" }}>${d}</div>`)}
      </div>
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: "4px" }}>
        ${cells.map((d, i) => {
          const info = cellInfo(d);
          return html`
            <div key=${i} style=${{
              aspectRatio: "1", borderRadius: "6px", border: "1px solid var(--border)",
              display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
              fontSize: "10px", visibility: d ? "visible" : "hidden", ...cellStyle(d),
            }}>
              ${d && html`<span style=${{ color: "var(--text-dim)" }}>${d}</span>`}
              ${info && html`<span class="mono" style=${{ fontSize: "9px", fontWeight: 700, color: info.pnl >= 0 ? "var(--bid-bright)" : "var(--ask-bright)" }}>${info.pnl >= 0 ? "+" : ""}${fmtMoney(info.pnl, { decimals: 0 })}</span>`}
            </div>
          `;
        })}
      </div>
    </div>
  `;
}
