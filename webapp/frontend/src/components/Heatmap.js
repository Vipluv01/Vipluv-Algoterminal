import React from "react";
import { html } from "../html.js";

// Brand-consistent diverging scale endpoints (same hues as --pnl-neg/
// --pnl-pos in theme.css) rather than generic heatmap red/blue -- a stress
// matrix is still a P&L concept, so it should read with the same color
// language as every P&L figure elsewhere in the app. Defined as real RGB
// triples (not CSS var() references) because per-cell text contrast below
// needs to compute against the ACTUAL rendered color, which requires
// numbers to interpolate and measure, not an opaque var().
const NEG_RGB = [226, 88, 58]; // --pnl-neg
const POS_RGB = [47, 191, 143]; // --pnl-pos
const NEUTRAL_RGB = [20, 27, 42]; // --surface-2, the zero point

const DARK_INK = [8, 10, 15]; // ~--bg
const LIGHT_INK = [232, 236, 244]; // ~--text

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function lerpRgb(rgbA, rgbB, t) {
  return [lerp(rgbA[0], rgbB[0], t), lerp(rgbA[1], rgbB[1], t), lerp(rgbA[2], rgbB[2], t)];
}

function defaultDivergingScale(value, maxAbs) {
  if (maxAbs === 0) return NEUTRAL_RGB;
  const t = Math.max(-1, Math.min(1, value / maxAbs));
  return t < 0 ? lerpRgb(NEUTRAL_RGB, NEG_RGB, -t) : lerpRgb(NEUTRAL_RGB, POS_RGB, t);
}

// WCAG relative luminance + contrast ratio -- the real formula (sRGB
// gamma-corrected channel weights), not an eyeballed "is it dark-ish"
// heuristic. See theme.css's own file-header comment: every color pairing
// elsewhere in this app was checked against this exact math, and a
// per-cell computed background here deserves the same rigor, not an
// assumption that white text is "probably fine."
function relativeLuminance([r, g, b]) {
  const [rs, gs, bs] = [r, g, b].map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

function contrastRatio(rgbA, rgbB) {
  const lA = relativeLuminance(rgbA) + 0.05;
  const lB = relativeLuminance(rgbB) + 0.05;
  return lA > lB ? lA / lB : lB / lA;
}

function textColorFor(bgRgb) {
  const darkContrast = contrastRatio(bgRgb, DARK_INK);
  const lightContrast = contrastRatio(bgRgb, LIGHT_INK);
  return darkContrast >= lightContrast ? `rgb(${DARK_INK.join(",")})` : `rgb(${LIGHT_INK.join(",")})`;
}

export function Heatmap({ rows, cols, values, colorScale, format }) {
  const flat = values.flat().filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  const maxAbs = flat.length ? Math.max(...flat.map(Math.abs)) : 1;
  const scale = colorScale || ((v) => defaultDivergingScale(v, maxAbs));
  const fmt = format || ((v) => (v === null || v === undefined ? "—" : v.toFixed(2)));

  return html`
    <div style=${{ overflowX: "auto" }}>
      <div style=${{ display: "grid", gridTemplateColumns: `120px repeat(${cols.length}, 1fr)`, gap: "2px", minWidth: `${120 + cols.length * 70}px` }}>
        <div />
        ${cols.map((c) => html`
          <div key=${c} style=${{ fontSize: "10px", color: "var(--text-faint)", textAlign: "center", padding: "4px 2px", fontWeight: 700 }}>${c}</div>
        `)}
        ${rows.map((r, ri) => html`
          <${React.Fragment} key=${r}>
            <div style=${{ fontSize: "11px", color: "var(--text-dim)", display: "flex", alignItems: "center", padding: "0 6px", fontWeight: 600 }}>${r}</div>
            ${cols.map((c, ci) => {
              const v = values[ri]?.[ci];
              const rgb = v === null || v === undefined || Number.isNaN(v) ? NEUTRAL_RGB : scale(v);
              const bg = `rgb(${rgb.map((n) => Math.round(n)).join(",")})`;
              return html`
                <div key=${c} class="mono" title=${`${r} / ${c}: ${fmt(v)}`}
                     style=${{
                       background: bg, color: textColorFor(rgb),
                       display: "flex", alignItems: "center", justifyContent: "center",
                       height: "36px", fontSize: "11px", fontWeight: 600, borderRadius: "3px",
                     }}>
                  ${fmt(v)}
                </div>
              `;
            })}
          <//>
        `)}
      </div>
    </div>
  `;
}
