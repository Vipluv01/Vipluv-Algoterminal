import React from "react";
import { html } from "../html.js";

// A signed value diverges from a ZERO baseline, not a bottom baseline --
// a P&L bar has to show its sign geometrically (which side of zero it's
// on), not only by color, matching the app's own "color is never the
// only channel" rule (see theme.css's --pnl-pos/--pnl-neg comment).
export function BarChart({ data, horizontal = false, colorFor, height = 220 }) {
  if (!data || !data.length) {
    return html`<div style=${{ height: `${height}px`, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)", fontSize: "12px" }}>No data</div>`;
  }

  const defaultColorFor = (d) => (d.value >= 0 ? "var(--pnl-pos)" : "var(--pnl-neg)");
  const getColor = colorFor || defaultColorFor;

  const values = data.map((d) => d.value);
  const maxAbs = Math.max(1e-9, ...values.map((v) => Math.abs(v)));
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const range = max - min || 1;

  if (horizontal) {
    const W = 600, rowH = 28, gap = 6;
    const H = data.length * (rowH + gap);
    const zeroX = (0 - min) / range * W;
    return html`
      <svg viewBox=${`0 0 ${W} ${H}`} style=${{ width: "100%", height: `${H}px`, display: "block" }} preserveAspectRatio="none">
        <line x1=${zeroX} x2=${zeroX} y1="0" y2=${H} stroke="var(--rule-strong)" stroke-width="1" />
        ${data.map((d, i) => {
          const barX = (Math.min(0, d.value) - min) / range * W;
          const barW = Math.abs(d.value) / range * W;
          const y = i * (rowH + gap);
          return html`
            <g key=${d.label}>
              <rect x=${barX} y=${y} width=${Math.max(1, barW)} height=${rowH} fill=${getColor(d)} rx="2" />
              <text x=${d.value >= 0 ? barX + barW + 6 : barX - 6} y=${y + rowH / 2 + 4}
                    text-anchor=${d.value >= 0 ? "start" : "end"} font-size="11" fill="var(--text-dim)" font-family="var(--font-mono)">
                ${d.label}
              </text>
            </g>
          `;
        })}
      </svg>
    `;
  }

  const W = 600, barGap = 8;
  const barW = data.length ? (W - barGap * (data.length - 1)) / data.length : W;
  const zeroY = height - ((0 - min) / range) * height;
  return html`
    <svg viewBox=${`0 0 ${W} ${height}`} style=${{ width: "100%", height: `${height}px`, display: "block" }} preserveAspectRatio="none">
      <line x1="0" x2=${W} y1=${zeroY} y2=${zeroY} stroke="var(--rule-strong)" stroke-width="1" />
      ${data.map((d, i) => {
        const x = i * (barW + barGap);
        const barH = Math.abs(d.value) / range * height;
        const y = d.value >= 0 ? zeroY - barH : zeroY;
        return html`
          <g key=${d.label}>
            <rect x=${x} y=${y} width=${barW} height=${Math.max(1, barH)} fill=${getColor(d)} rx="2" />
          </g>
        `;
      })}
    </svg>
    <div style=${{ display: "flex", marginTop: "4px" }}>
      ${data.map((d) => html`
        <span key=${d.label} style=${{ flex: 1, textAlign: "center", fontSize: "10px", color: "var(--text-faint)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>${d.label}</span>
      `)}
    </div>
  `;
}
