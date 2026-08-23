import React from "react";
import { html } from "../html.js";
import { fmtMoney } from "../format.js";

function DepthRows({ levels, side }) {
  if (!levels.length) {
    return html`<div class="row" style=${{ color: "var(--text-faint)", justifyContent: "center" }}>no resting orders</div>`;
  }
  const maxQty = Math.max(...levels.map((l) => l.qty));
  const rows = levels.map((l) => html`
    <div key=${l.px} class=${`depth-row ${side}`}>
      <div class="depth-bar" style=${{ width: `${Math.max(6, (l.qty / maxQty) * 100)}%` }} />
      <span>${fmtMoney(l.px)}</span>
      <span class="mono">${l.qty}</span>
    </div>
  `);
  return html`<${React.Fragment}>${side === "ask" ? [...rows].reverse() : rows}<//>`;
}

export function OrderBook({ tick }) {
  const bids = tick?.bids || [];
  const asks = tick?.asks || [];
  const mid = tick?.best_bid && tick?.best_ask ? (tick.best_bid + tick.best_ask) / 2 : tick?.price;
  const spread = tick?.best_bid && tick?.best_ask ? tick.best_ask - tick.best_bid : null;

  return html`
    <div class="panel panel-pad">
      <div class="panel-title">
        <span>Order Book</span>
        ${spread !== null && html`<span class="mono" style=${{ color: "var(--text-faint)", fontWeight: 500 }}>spread ${fmtMoney(spread)}</span>`}
      </div>
      <${DepthRows} levels=${asks} side="ask" />
      <div class="mid-row">${mid ? fmtMoney(mid) : "—"}</div>
      <${DepthRows} levels=${bids} side="bid" />
    </div>
  `;
}
