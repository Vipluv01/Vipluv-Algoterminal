import React from "react";
import { html } from "../html.js";

const Z_MIN = -4, Z_MAX = 4;
const SAMPLES = 161; // every 0.05 z -- smooth enough at any reasonable render width

function phi(z) {
  return Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
}

// The gauge's job is showing position relative to YOUR OWN bands (entry/
// stop), not abstract normality -- entryZ/stopZ are drawn as vertical
// rules through the SAME curve the current z-needle sits on, so "how far
// is this trade from its stop" reads directly off the same picture as
// "how extreme is this z-score," rather than needing two separate charts.
export function BellCurve({ zScore, mean = 0, stdDev = 1, entryZ, stopZ, height = 180 }) {
  const W = 600, H = height;
  const padY = 16;
  const maxPhi = phi(0);
  const x = (z) => ((z - Z_MIN) / (Z_MAX - Z_MIN)) * W;
  const y = (p) => H - padY - (p / maxPhi) * (H - padY * 2);

  const points = [];
  for (let i = 0; i < SAMPLES; i++) {
    const z = Z_MIN + (i / (SAMPLES - 1)) * (Z_MAX - Z_MIN);
    points.push([z, phi(z)]);
  }
  const curvePath = points.map(([z, p], i) => `${i === 0 ? "M" : "L"}${x(z).toFixed(1)},${y(p).toFixed(1)}`).join(" ");

  const hasZ = zScore !== null && zScore !== undefined && !Number.isNaN(zScore);
  const clampedZ = hasZ ? Math.max(Z_MIN, Math.min(Z_MAX, zScore)) : null;

  // Shaded tail: the area under the curve beyond the current |z| -- the
  // classic "how extreme is this" read. Shades whichever side z actually
  // sits on.
  let tailPath = null;
  if (hasZ) {
    const tailPoints = points.filter(([z]) => (clampedZ >= 0 ? z >= clampedZ : z <= clampedZ));
    if (tailPoints.length > 1) {
      const start = tailPoints[0];
      tailPath =
        `M${x(start[0]).toFixed(1)},${y(0).toFixed(1)} ` +
        tailPoints.map(([z, p]) => `L${x(z).toFixed(1)},${y(p).toFixed(1)}`).join(" ") +
        ` L${x(tailPoints[tailPoints.length - 1][0]).toFixed(1)},${y(0).toFixed(1)} Z`;
    }
  }

  function verticalRule(z, color, label) {
    if (z === null || z === undefined || Number.isNaN(z)) return null;
    const cz = Math.max(Z_MIN, Math.min(Z_MAX, z));
    return html`
      <g>
        <line x1=${x(cz)} x2=${x(cz)} y1=${padY} y2=${H - padY} stroke=${color} stroke-width="1.5" stroke-dasharray="4 3" />
        <text x=${x(cz)} y=${padY - 4} text-anchor="middle" font-size="9.5" fill=${color} font-family="var(--font-mono)">${label}</text>
      </g>
    `;
  }

  return html`
    <div>
      <svg viewBox=${`0 0 ${W} ${H}`} style=${{ width: "100%", height: `${H}px`, display: "block" }} preserveAspectRatio="none">
        <line x1=${x(0)} x2=${x(0)} y1=${padY} y2=${H - padY} stroke="var(--rule)" stroke-width="1" />
        <path d=${curvePath} fill="none" stroke="var(--ink-soft)" stroke-width="1.5" vector-effect="non-scaling-stroke" />
        ${tailPath && html`<path d=${tailPath} fill="var(--accent-dim)" stroke="none" />`}
        ${verticalRule(entryZ, "var(--pnl-pos)", "ENTRY")}
        ${verticalRule(stopZ, "var(--pnl-neg)", "STOP")}
        ${hasZ && html`
          <g>
            <line x1=${x(clampedZ)} x2=${x(clampedZ)} y1=${padY} y2=${H - padY} stroke="var(--accent)" stroke-width="2" />
            <circle cx=${x(clampedZ)} cy=${y(phi(clampedZ))} r="4" fill="var(--accent-bright)" />
          </g>
        `}
      </svg>
      <div style=${{ display: "flex", justifyContent: "space-between", marginTop: "4px", fontSize: "10.5px", color: "var(--text-faint)" }}>
        <span>z = ${hasZ ? zScore.toFixed(2) : "—"}</span>
        <span>μ=${mean.toFixed(2)} σ=${stdDev.toFixed(2)}</span>
      </div>
    </div>
  `;
}
