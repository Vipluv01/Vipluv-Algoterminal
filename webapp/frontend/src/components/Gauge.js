import React from "react";
import { html } from "../html.js";

function polarToCartesian(cx, cy, r, angleDeg) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function describeArc(cx, cy, r, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, r, startAngle);
  const end = polarToCartesian(cx, cy, r, endAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`;
}

// A half-circle gauge, e.g. for Profit Factor: 0 reads as fully "bad" (red)
// at the left, `goodAt` and beyond reads as fully "good" (green) at the
// right, with a break-even marker at `markAt` -- gross-win/gross-loss = 1
// is the actual break-even point for profit factor specifically, not a
// decorative midpoint, so the marker's position is meaningful, not just
// styling.
export function Gauge({ value, max = 3, markAt = 1, label, size = 120 }) {
  const cx = size / 2, cy = size / 2, r = size / 2 - 14;
  const clamped = value === null || value === undefined ? 0 : Math.max(0, Math.min(value, max));
  const frac = clamped / max;
  const valueAngle = 180 + frac * 180;
  const markFrac = Math.max(0, Math.min(markAt / max, 1));
  const markAngle = 180 + markFrac * 180;
  const markPos = polarToCartesian(cx, cy, r, markAngle);
  const markPosInner = polarToCartesian(cx, cy, r - 8, markAngle);

  const color = value === null || value === undefined ? "var(--text-faint)"
    : value >= markAt * 1.5 ? "var(--bid-bright)"
    : value >= markAt ? "#f5c542"
    : "var(--ask-bright)";

  return html`
    <div style=${{ display: "flex", flexDirection: "column", alignItems: "center" }}>
      <svg viewBox=${`0 0 ${size} ${size / 2 + 14}`} style=${{ width: `${size}px`, height: `${size / 2 + 14}px` }}>
        <path d=${describeArc(cx, cy, r, 180, 360)} fill="none" stroke="var(--surface-2)" stroke-width="10" stroke-linecap="round" />
        ${clamped > 0 && html`<path d=${describeArc(cx, cy, r, 180, valueAngle)} fill="none" stroke=${color} stroke-width="10" stroke-linecap="round" />`}
        <line x1=${markPos.x} y1=${markPos.y} x2=${markPosInner.x} y2=${markPosInner.y} stroke="var(--text-faint)" stroke-width="2" />
        <text x=${cx} y=${cy - 4} text-anchor="middle" fill="var(--text)" font-size="20" font-weight="700" font-family="var(--font-mono)">
          ${value !== null && value !== undefined ? value.toFixed(2) : "—"}
        </text>
      </svg>
      ${label && html`<div class="stat-sub" style=${{ marginTop: "-4px" }}>${label}</div>`}
    </div>
  `;
}
