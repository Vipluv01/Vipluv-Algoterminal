import React from "react";
import { html } from "../html.js";

// LineChart.js is single-series and shares its one scale across a whole
// panel by construction. This generalizes to N series with a legend and a
// crosshair shared across all of them (one mouse position, every series'
// value at that x, read together) -- and, unlike stacking multiple
// LineChart instances, an OPT-IN independent y-scale per series
// (series.independentScale), so a spread (a small range around zero) can
// overlay a price (a large range far from zero) without either one
// flattening the other into a near-flat line at the shared scale.
export function MultiLineChart({ series, yFormat, crosshair = true, height = 240 }) {
  const W = 1000, H = height;
  const [hoverIdx, setHoverIdx] = React.useState(null);
  const svgRef = React.useRef(null);

  const cleanSeries = (series || []).filter((s) => s.points && s.points.length >= 2);
  if (!cleanSeries.length) {
    return html`<div style=${{ height: `${H}px`, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)", fontSize: "12px" }}>Not enough history yet</div>`;
  }

  const n = Math.max(...cleanSeries.map((s) => s.points.length));
  const x = (i) => (i / (n - 1)) * W;

  // Shared-scale series share one min/max; independentScale series each
  // get their own, normalized into the same pixel range so they're still
  // visually comparable in SHAPE (both cross their own midline together)
  // even though their raw units differ.
  const shared = cleanSeries.filter((s) => !s.independentScale);
  const sharedValues = shared.flatMap((s) => s.points.filter((v) => v !== null && v !== undefined));
  let sharedMin = sharedValues.length ? Math.min(...sharedValues) : 0;
  let sharedMax = sharedValues.length ? Math.max(...sharedValues) : 1;
  if (sharedMin === sharedMax) { sharedMin -= 1; sharedMax += 1; }
  const sharedPad = (sharedMax - sharedMin) * 0.08;
  sharedMin -= sharedPad; sharedMax += sharedPad;

  function scaleFor(s) {
    if (!s.independentScale) {
      return (v) => H - ((v - sharedMin) / (sharedMax - sharedMin)) * H;
    }
    const values = s.points.filter((v) => v !== null && v !== undefined);
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; }
    const pad = (max - min) * 0.08;
    min -= pad; max += pad;
    return (v) => H - ((v - min) / (max - min)) * H;
  }

  function pathFor(s) {
    const yFn = scaleFor(s);
    let d = "", drawing = false;
    s.points.forEach((v, i) => {
      if (v === null || v === undefined || Number.isNaN(v)) { drawing = false; return; }
      d += `${drawing ? "L" : "M"}${x(i).toFixed(1)},${yFn(v).toFixed(1)} `;
      drawing = true;
    });
    return d;
  }

  function onMouseMove(e) {
    if (!crosshair || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * W;
    const idx = Math.max(0, Math.min(n - 1, Math.round((relX / W) * (n - 1))));
    setHoverIdx(idx);
  }

  const fmt = yFormat || ((v) => (v === null || v === undefined ? "—" : v.toFixed(2)));

  return html`
    <div>
      <div style=${{ display: "flex", gap: "14px", marginBottom: "6px", flexWrap: "wrap" }}>
        ${cleanSeries.map((s) => html`
          <span key=${s.name} style=${{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "11px", color: "var(--text-dim)" }}>
            <span style=${{ width: "10px", height: "2px", background: s.color || "var(--accent-bright)", display: "inline-block" }} />
            ${s.name}${s.independentScale ? " (own scale)" : ""}
          </span>
        `)}
      </div>
      <svg ref=${svgRef} viewBox=${`0 0 ${W} ${H}`} style=${{ width: "100%", height: `${H}px`, display: "block", cursor: crosshair ? "crosshair" : "default" }}
           preserveAspectRatio="none" onMouseMove=${onMouseMove} onMouseLeave=${() => setHoverIdx(null)}>
        ${cleanSeries.map((s) => html`
          <path key=${s.name} d=${pathFor(s)} fill="none" stroke=${s.color || "var(--accent-bright)"} stroke-width="1.75" vector-effect="non-scaling-stroke" />
        `)}
        ${crosshair && hoverIdx !== null && html`
          <line x1=${x(hoverIdx)} x2=${x(hoverIdx)} y1="0" y2=${H} stroke="var(--rule-strong)" stroke-width="1" stroke-dasharray="3 3" />
        `}
      </svg>
      ${crosshair && hoverIdx !== null && html`
        <div style=${{ display: "flex", gap: "14px", marginTop: "4px", flexWrap: "wrap", fontSize: "11px" }} class="mono">
          ${cleanSeries.map((s) => html`
            <span key=${s.name} style=${{ color: s.color || "var(--accent-bright)" }}>${s.name}: ${fmt(s.points[hoverIdx])}</span>
          `)}
        </div>
      `}
    </div>
  `;
}
