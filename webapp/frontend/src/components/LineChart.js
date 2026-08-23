import React from "react";
import { html } from "../html.js";

// A minimal SVG line chart for a single numeric series (nulls become gaps
// in the line, e.g. the leading points of a rolling window before it has
// enough history) plus optional horizontal threshold bands -- built from
// scratch rather than pulling in klinecharts for this, since klinecharts
// is a candlestick-focused library and these are plain statistical time
// series (z-score, hedge ratio), not OHLCV data.
export function LineChart({ series, height = 160, color = "var(--accent-bright)", bands = [], fillLast = true }) {
  const W = 1000, H = 300;
  const clean = series.filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (clean.length < 2) {
    return html`<div style=${{ height: `${height}px`, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)", fontSize: "12px" }}>Not enough history yet</div>`;
  }

  const bandValues = bands.map((b) => b.value);
  const allValues = [...clean, ...bandValues];
  let min = Math.min(...allValues), max = Math.max(...allValues);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.08;
  min -= pad; max += pad;

  const n = series.length;
  const x = (i) => (i / (n - 1)) * W;
  const y = (v) => H - ((v - min) / (max - min)) * H;

  let d = "";
  let drawing = false;
  series.forEach((v, i) => {
    if (v === null || v === undefined || Number.isNaN(v)) { drawing = false; return; }
    d += `${drawing ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
    drawing = true;
  });

  const lastIdx = series.map((v, i) => (v !== null && v !== undefined && !Number.isNaN(v) ? i : -1)).filter((i) => i >= 0).pop();
  const lastVal = series[lastIdx];

  return html`
    <svg viewBox=${`0 0 ${W} ${H}`} style=${{ width: "100%", height: `${height}px`, display: "block" }} preserveAspectRatio="none">
      ${bands.map((b, i) => html`
        <g key=${i}>
          <line x1="0" x2=${W} y1=${y(b.value)} y2=${y(b.value)} stroke=${b.color || "var(--border-bright)"}
                stroke-width="1" stroke-dasharray="4 4" opacity="0.7" />
        </g>
      `)}
      <path d=${d} fill="none" stroke=${color} stroke-width="2" vector-effect="non-scaling-stroke" />
      ${fillLast && lastIdx !== undefined && html`
        <circle cx=${x(lastIdx)} cy=${y(lastVal)} r="4" fill=${color} />
      `}
    </svg>
    <div style=${{ display: "flex", justifyContent: "space-between", marginTop: "4px" }}>
      ${bands.map((b, i) => html`
        <span key=${i} style=${{ fontSize: "10px", color: "var(--text-faint)" }}>${b.label}</span>
      `)}
    </div>
  `;
}
