import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtNum, fmtPct } from "../format.js";

function ControlCard({ label, value, body }) {
  return html`
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class="stat-value mono">${value}</div>
      <div class="stat-sub">${body}</div>
    </div>
  `;
}

export function Risk() {
  const [risk, setRisk] = React.useState(null);
  React.useEffect(() => { api.risk().then(setRisk); }, []);

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Risk</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>
        What the system actually enforces on every order, not aspirational settings that live only in a doc.
      </div>

      ${!risk
        ? html`<div class="skeleton" style=${{ height: "160px" }} />`
        : html`
          <div style=${{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px" }} class="dash-stats">
            <${ControlCard} label="Max Order Quantity" value=${fmtNum(risk.max_order_qty)}
              body="Hard ceiling on every single order, paper or live — independent of any strategy's own sizing logic." />
            <${ControlCard} label="Fractional Kelly Multiplier" value=${fmtPct(risk.kelly_multiplier)}
              body="Applied on top of the full Kelly fraction — full Kelly is growth-optimal but high-variance under estimation error, so this scales it down." />
            <${ControlCard} label="Max Position Fraction" value=${fmtPct(risk.max_position_fraction)}
              body="Hard ceiling on account fraction per position, independent of what the Kelly math computes — no single calculation is trusted to bound itself." />
            <${ControlCard} label="Pairs Entry Z-Score" value=${`±${risk.pairs_entry_z}σ`}
              body="Spread must deviate at least this many standard deviations from its rolling mean before the pairs strategy enters." />
            <${ControlCard} label="Pairs Stop Z-Score" value=${`±${risk.pairs_stop_z}σ`}
              body="Force-closes the position regardless of the normal exit threshold — takes priority over everything else." />
            <${ControlCard} label="Cointegration p-value Ceiling" value=${risk.pairs_coint_pvalue_max}
              body="Engle-Granger test must clear this before any pairs signal fires at all — the check the old Algo Terminal skipped." />
          </div>
        `}
    </div>
  `;
}
