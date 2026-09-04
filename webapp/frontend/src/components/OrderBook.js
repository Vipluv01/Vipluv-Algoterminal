import React from "react";
import { html } from "../html.js";
import { fmtMoney, fmtNum } from "../format.js";

// Cumulative resting quantity from best price outward -- same running-
// total DepthChart already computes for its area fill, surfaced here as a
// third number per row: "how much size is available AT OR BETTER than
// this level," which is what a trader sizing an order against the book
// actually needs, not just what's resting at one specific price.
function DepthRows({ levels, side, onLevelClick, flashes, loading }) {
  if (!levels.length) {
    return html`<div class="row" style=${{ color: "var(--text-faint)", justifyContent: "center" }}>${loading ? "Loading order book…" : "no resting orders"}</div>`;
  }
  const maxQty = Math.max(...levels.map((l) => l.qty));
  let running = 0;
  const withCum = levels.map((l) => {
    running += l.qty;
    return { ...l, cum: running };
  });
  const rows = withCum.map((l) => html`
    <div key=${l.px} class=${`depth-row ${side} clickable ${flashes && flashes.has(`${side}:${l.px}`) ? `flash-${side}` : ""}`}
         onClick=${() => onLevelClick && onLevelClick(side === "ask" ? "buy" : "sell", l.px)}
         title=${`${side === "ask" ? "Buy" : "Sell"} at ${fmtMoney(l.px)} (prefills the ticket)`}>
      <div class="depth-bar" style=${{ width: `${Math.max(6, (l.qty / maxQty) * 100)}%` }} />
      <span>${fmtMoney(l.px)}</span>
      <span class="mono">${l.qty}</span>
      <span class="mono" style=${{ color: "var(--text-faint)" }}>${l.cum}</span>
    </div>
  `);
  return html`<${React.Fragment}>${side === "ask" ? [...rows].reverse() : rows}<//>`;
}

// Flags price levels whose resting qty actually changed since the last tick
// (or that are newly resting), for a brief flash highlight -- compared by
// VALUE (a Map snapshot), not object identity, since bids/asks arrive as
// fresh arrays every tick regardless of whether anything really moved.
// Skips the very first tick (nothing to diff against yet), or the whole
// book would flash once on initial load.
function useFlashLevels(bids, asks) {
  const prevRef = React.useRef(null);
  const [flashes, setFlashes] = React.useState(() => new Set());

  React.useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = {
      bid: new Map(bids.map((l) => [l.px, l.qty])),
      ask: new Map(asks.map((l) => [l.px, l.qty])),
    };
    if (!prev) return;
    const next = new Set();
    bids.forEach((l) => { if (prev.bid.get(l.px) !== l.qty) next.add(`bid:${l.px}`); });
    asks.forEach((l) => { if (prev.ask.get(l.px) !== l.qty) next.add(`ask:${l.px}`); });
    if (next.size === 0) return;
    setFlashes(next);
    const id = setTimeout(() => setFlashes(new Set()), 550);
    return () => clearTimeout(id);
  }, [bids, asks]);

  return flashes;
}

// Classic order-book depth chart: cumulative resting quantity as a function
// of distance from mid price, both sides sharing one Y scale so their
// relative size is directly comparable at a glance -- not just two
// separate bar lists (DepthRows above), the actual point of a depth view.
function DepthChart({ bids, asks, loading }) {
  const W = 500, H = 160;
  if (!bids.length && !asks.length) {
    return html`<div style=${{ height: `${H}px`, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)", fontSize: "12px" }}>${loading ? "Loading order book…" : "No resting orders"}</div>`;
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

export function OrderBook({ tick, stale = false, onLevelClick }) {
  const [view, setView] = React.useState("book");
  // Distinguishes "no tick has arrived at all yet" (tick === undefined/null
  // -- e.g. the gap between selecting a symbol and the WS delivering its
  // first message) from "a tick DID arrive and the book is genuinely
  // empty right now" (tick.bids/asks === [], a real, honest state -- e.g.
  // a derived index with no order book at all). Both used to render the
  // identical "no resting orders" text, which read as a broken/missing
  // book during the loading gap specifically -- this is that fix.
  const loading = tick == null;
  const bids = tick?.bids || [];
  const asks = tick?.asks || [];
  const mid = tick?.best_bid && tick?.best_ask ? (tick.best_bid + tick.best_ask) / 2 : tick?.price;
  const spread = tick?.best_bid && tick?.best_ask ? tick.best_ask - tick.best_bid : null;

  const totalBidQty = bids.reduce((s, l) => s + l.qty, 0);
  const totalAskQty = asks.reduce((s, l) => s + l.qty, 0);
  const totalQty = totalBidQty + totalAskQty;
  const bidPct = totalQty > 0 ? (totalBidQty / totalQty) * 100 : 50;
  const flashes = useFlashLevels(bids, asks);

  return html`
    <div class=${`panel panel-pad ${stale ? "is-stale" : ""}`}>
      <div class="tabs" style=${{ marginBottom: "10px" }}>
        <div class=${`tab ${view === "book" ? "active" : ""}`} onClick=${() => setView("book")}>Book</div>
        <div class=${`tab ${view === "depth" ? "active" : ""}`} onClick=${() => setView("depth")}>Depth</div>
        ${spread !== null && html`<div style=${{ marginLeft: "auto", alignSelf: "center", color: "var(--text-faint)", fontSize: "11px" }} class="mono">spread ${fmtMoney(spread)}</div>`}
      </div>

      ${view === "book"
        ? html`
          <${React.Fragment}>
            <div class="row" style=${{ color: "var(--text-faint)", fontSize: "10px", padding: "0 8px" }}>
              <span>Price</span><span class="mono">Qty</span><span class="mono">Cum</span>
            </div>
            <${DepthRows} levels=${asks} side="ask" onLevelClick=${onLevelClick} flashes=${flashes} loading=${loading} />
            <div class="mid-row">${mid ? fmtMoney(mid) : "—"}</div>
            <${DepthRows} levels=${bids} side="bid" onLevelClick=${onLevelClick} flashes=${flashes} loading=${loading} />
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
            <${DepthChart} bids=${bids} asks=${asks} loading=${loading} />
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
