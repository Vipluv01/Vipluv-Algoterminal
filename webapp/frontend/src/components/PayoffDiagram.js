import React from "react";
import { html } from "../html.js";

// Per-leg payoff at expiry for one unit of underlying move -- qty encodes
// direction directly (positive = long, negative = short), so the same
// formula (intrinsic value minus premium, scaled by qty) is correct for
// both a long leg (pays premium, keeps the intrinsic) and a short leg
// (receives premium, owes the intrinsic) without a separate long/short
// branch: a short leg's negative qty flips the sign of both terms
// together, which is exactly what "receive premium, owe the payout" means.
function legPayoff(leg, spotAtExpiry) {
  const intrinsic = leg.type === "call" ? Math.max(0, spotAtExpiry - leg.strike) : Math.max(0, leg.strike - spotAtExpiry);
  return leg.qty * (intrinsic - leg.premium);
}

function totalPayoff(legs, spotAtExpiry) {
  return legs.reduce((sum, leg) => sum + legPayoff(leg, spotAtExpiry), 0);
}

// Piecewise-linear payoff at expiry, spot marked as a vertical rule,
// profit region shaded above the zero line, breakevens found by scanning
// for sign changes and linearly interpolating -- exact, not approximated,
// since the payoff is genuinely piecewise-LINEAR (kinks only at strikes),
// so linear interpolation between two adjacent sample points either side
// of a sign change is the true crossing, not an estimate.
export function PayoffDiagram({ legs, spot, showBreakevens = true, height = 240 }) {
  if (!legs || !legs.length) {
    return html`<div style=${{ height: `${height}px`, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)", fontSize: "12px" }}>No legs</div>`;
  }

  const strikes = legs.map((l) => l.strike);
  const lo = Math.min(spot, ...strikes) * 0.85;
  const hi = Math.max(spot, ...strikes) * 1.15;
  const SAMPLES = 200;
  const samples = [];
  for (let i = 0; i <= SAMPLES; i++) {
    const s = lo + (i / SAMPLES) * (hi - lo);
    samples.push([s, totalPayoff(legs, s)]);
  }

  const breakevens = [];
  if (showBreakevens) {
    for (let i = 1; i < samples.length; i++) {
      const [s0, p0] = samples[i - 1];
      const [s1, p1] = samples[i];
      if ((p0 <= 0 && p1 > 0) || (p0 >= 0 && p1 < 0)) {
        const t = p0 === p1 ? 0 : -p0 / (p1 - p0);
        breakevens.push(s0 + t * (s1 - s0));
      }
    }
  }

  const W = 700, H = height, padX = 12;
  const values = samples.map(([, p]) => p);
  let min = Math.min(0, ...values), max = Math.max(0, ...values);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.1;
  min -= pad; max += pad;

  const x = (s) => padX + ((s - lo) / (hi - lo)) * (W - padX * 2);
  const y = (p) => H - ((p - min) / (max - min)) * H;
  const zeroY = y(0);

  const linePath = samples.map(([s, p], i) => `${i === 0 ? "M" : "L"}${x(s).toFixed(1)},${y(p).toFixed(1)}`).join(" ");

  // Profit-region fill: the curve clipped to the area above zero, closed
  // back along the zero line -- not the whole area under the curve, which
  // would also shade the loss region in the same color.
  const profitPath =
    `M${x(lo).toFixed(1)},${zeroY.toFixed(1)} ` +
    samples.map(([s, p]) => `L${x(s).toFixed(1)},${y(Math.max(0, p)).toFixed(1)}`).join(" ") +
    ` L${x(hi).toFixed(1)},${zeroY.toFixed(1)} Z`;
  const lossPath =
    `M${x(lo).toFixed(1)},${zeroY.toFixed(1)} ` +
    samples.map(([s, p]) => `L${x(s).toFixed(1)},${y(Math.min(0, p)).toFixed(1)}`).join(" ") +
    ` L${x(hi).toFixed(1)},${zeroY.toFixed(1)} Z`;

  return html`
    <svg viewBox=${`0 0 ${W} ${H}`} style=${{ width: "100%", height: `${H}px`, display: "block" }} preserveAspectRatio="none">
      <path d=${profitPath} fill="var(--pnl-pos-dim)" stroke="none" />
      <path d=${lossPath} fill="var(--pnl-neg-dim)" stroke="none" />
      <line x1=${padX} x2=${W - padX} y1=${zeroY} y2=${zeroY} stroke="var(--rule-strong)" stroke-width="1" />
      <line x1=${x(spot)} x2=${x(spot)} y1="0" y2=${H} stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="4 3" />
      <text x=${x(spot)} y="12" text-anchor="middle" font-size="9.5" fill="var(--accent-bright)" font-family="var(--font-mono)">SPOT ${spot.toFixed(2)}</text>
      ${breakevens.map((be, i) => html`
        <g key=${i}>
          <line x1=${x(be)} x2=${x(be)} y1="0" y2=${H} stroke="var(--ink-faint)" stroke-width="1" stroke-dasharray="2 3" />
          <text x=${x(be)} y=${H - 4} text-anchor="middle" font-size="9.5" fill="var(--ink-soft)" font-family="var(--font-mono)">${be.toFixed(2)}</text>
        </g>
      `)}
      <path d=${linePath} fill="none" stroke="var(--ink)" stroke-width="2" vector-effect="non-scaling-stroke" />
    </svg>
  `;
}
