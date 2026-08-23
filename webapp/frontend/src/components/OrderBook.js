import React from "react";
import { html } from "../html.js";
import { fmtMoney, fmtNum } from "../format.js";

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

// Classic order-book depth chart: cumulative resting quantity as a function
// of distance from mid price, both sides sharing one Y scale so their
// relative size is directly comparable at a glance -- not just two
// separate bar lists (DepthRows above), the actual point of a depth view.
function DepthChart({ bids, asks }) {
  const W = 500, H = 160;
  if (!bids.length && !asks.length) {
    return html`<div style=${{ height: `${H}px`, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)", fontSize: "12px" }}>No resting orders</div>`;
  }

  // bids/asks already arrive best-first (closest to mid) per the backend's
  // own depth() ordering -- cumulative sum walks outward from mid, which
  // is exactly the quantity "available within N ticks of the touch".
  let running = 0;
  const bidCum = bids.map((l) => (running += l.qty));
  running = 0;
  const askCum = asks.map((l) => (running += l.qty));
  const maxCum = Math.max(1, ...bidCum, ...askCum);

  const halfW = W / 2;
  const stepBid = bids.length ? halfW / bids.length : halfW;
  const stepAsk = asks.length ? halfW / asks.length : halfW;
  const y = (cum) => H - (cum / maxCum) * (H - 4);

  // Bid area: staircase from mid (x=halfW, cum=0) outward to the left.
  let bidPath = `M${halfW},${H} L${halfW},${y(0)} `;
  bids.forEach((l, i) => {
    const x = halfW - i * stepBid;
    const xNext = halfW - (i + 1) * stepBid;
    bidPath += `L${x},${y(bidCum[i])} L${xNext},${y(bidCum[i])} `;
  });
  bidPath += `L${halfW - bids.length * stepBid},${H} Z`;

  let askPath = `M${halfW},${H} L${halfW},${y(0)} `;
  asks.forEach((l, i) => {
    const x = halfW + i * stepAsk;
    const xNext = halfW + (i + 1) * stepAsk;
    askPath += `L${x},${y(askCum[i])} L${xNext},${y(askCum[i])} `;
  });
  askPath += `L${halfW + asks.length * stepAsk},${H} Z`;

  return html`
    <svg viewBox=${`0 0 ${W} ${H}`} style=${{ width: "100%", height: `${H}px`, display: "block" }} preserveAspectRatio="none">
      <line x1=${halfW} x2=${halfW} y1="0" y2=${H} stroke="var(--border)" stroke-dasharray="3 3" />
      <path d=${bidPath} fill="var(--bid-dim)" stroke="var(--bid-bright)" stroke-width="1.5" />
      <path d=${askPath} fill="var(--ask-dim)" stroke="var(--ask-bright)" stroke-width="1.5" />
    </svg>
  `;
}

function Stat({ label, value, valueClass = "" }) {
  return html`
    <div>
      <div class="stat-label" style=${{ fontSize: "9px" }}>${label}</div>
      <div class=${`mono ${valueClass}`} style=${{ fontSize: "12px", fontWeight: 600 }}>${value}</div>
    </div>
  `;
}

export function OrderBook({ tick }) {
  const [view, setView] = React.useState("book");
  const bids = tick?.bids || [];
  const asks = tick?.asks || [];
  const mid = tick?.best_bid && tick?.best_ask ? (tick.best_bid + tick.best_ask) / 2 : tick?.price;
  const spread = tick?.best_bid && tick?.best_ask ? tick.best_ask - tick.best_bid : null;

  const totalBidQty = bids.reduce((s, l) => s + l.qty, 0);
  const totalAskQty = asks.reduce((s, l) => s + l.qty, 0);
  const totalQty = totalBidQty + totalAskQty;
  const bidPct = totalQty > 0 ? (totalBidQty / totalQty) * 100 : 50;

  return html`
    <div class="panel panel-pad">
      <div class="tabs" style=${{ marginBottom: "10px" }}>
        <div class=${`tab ${view === "book" ? "active" : ""}`} onClick=${() => setView("book")}>Book</div>
        <div class=${`tab ${view === "depth" ? "active" : ""}`} onClick=${() => setView("depth")}>Depth</div>
        ${spread !== null && html`<div style=${{ marginLeft: "auto", alignSelf: "center", color: "var(--text-faint)", fontSize: "11px" }} class="mono">spread ${fmtMoney(spread)}</div>`}
      </div>

      ${view === "book"
        ? html`
          <${React.Fragment}>
            <${DepthRows} levels=${asks} side="ask" />
            <div class="mid-row">${mid ? fmtMoney(mid) : "—"}</div>
            <${DepthRows} levels=${bids} side="bid" />
          <//>
        `
        : html`
          <${React.Fragment}>
            <div style=${{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px", marginBottom: "12px" }}>
              <${Stat} label="Best Bid" value=${tick?.best_bid ? fmtMoney(tick.best_bid) : "—"} valueClass="pos" />
              <${Stat} label="Mid" value=${mid ? fmtMoney(mid) : "—"} />
              <${Stat} label="Best Ask" value=${tick?.best_ask ? fmtMoney(tick.best_ask) : "—"} valueClass="neg" />
              <${Stat} label="Spread" value=${spread !== null ? fmtMoney(spread) : "—"} />
            </div>
            <${DepthChart} bids=${bids} asks=${asks} />
            <div style=${{ marginTop: "10px" }}>
              <div style=${{ display: "flex", justifyContent: "space-between", fontSize: "10.5px", marginBottom: "4px" }}>
                <span class="pos">BID ${bidPct.toFixed(0)}%</span>
                <span>DEPTH IMBALANCE</span>
                <span class="neg">${(100 - bidPct).toFixed(0)}% ASK</span>
              </div>
              <div style=${{ height: "6px", borderRadius: "4px", overflow: "hidden", display: "flex" }}>
                <div style=${{ width: `${bidPct}%`, background: "var(--bid-bright)" }} />
                <div style=${{ width: `${100 - bidPct}%`, background: "var(--ask-bright)" }} />
              </div>
            </div>
          <//>
        `}
    </div>
  `;
}
