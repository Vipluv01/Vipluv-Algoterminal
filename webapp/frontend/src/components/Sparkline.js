import React from "react";
import { html } from "../html.js";

// A minimal cumulative-value sparkline -- no axes, no labels, just the
// shape of the trend, meant to sit inline inside a stat card the way
// Bull's Net P&L card does. Color follows the FINAL value's sign, not
// per-segment, since this reads as "how did we end up here" at a glance,
// not a detailed chart (LineChart.js already covers that use case).
export function Sparkline({ values, height = 36, width = 120 }) {
  const clean = (values || []).filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (clean.length < 2) return null;

  const min = Math.min(...clean, 0);
  const max = Math.max(...clean, 0);
  const range = max - min || 1;
  const n = clean.length;
  const x = (i) => (i / (n - 1)) * width;
  const y = (v) => height - ((v - min) / range) * height;

  const points = clean.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" L");
  const positive = clean[clean.length - 1] >= 0;
  const color = positive ? "var(--bid-bright)" : "var(--ask-bright)";
  const areaPath = `M${points} L${width},${height} L0,${height} Z`;

  return html`
    <svg viewBox=${`0 0 ${width} ${height}`} style=${{ width: `${width}px`, height: `${height}px`, display: "block" }} preserveAspectRatio="none">
      <path d=${areaPath} fill=${color} opacity="0.12" />
      <path d=${`M${points}`} fill="none" stroke=${color} stroke-width="1.5" vector-effect="non-scaling-stroke" />
    </svg>
  `;
}
