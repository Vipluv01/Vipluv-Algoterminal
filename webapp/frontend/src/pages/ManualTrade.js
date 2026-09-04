import React from "react";
import { html } from "../html.js";
import { api, subscribeMarketForMode } from "../api.js";
import { fmtMoney } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";
import { BellCurve } from "../components/BellCurve.js";
import { OrderModeBanner } from "../components/OrderModeBanner.js";
import { LiveOrderConfirmModal } from "../components/LiveOrderConfirmModal.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { LiveSymbolSearch } from "../components/LiveSymbolSearch.js";
import { useToast } from "../toast.js";
import { consumeTicketIntent } from "../ticketIntent.js";
// Aliased -- this page's own `mode` state already means single/spread
// ticket UI, unrelated to the real trading mode (paper/virtual/live).
import { useMode as useTradingMode } from "../mode.js";

const PAIRS_SYMBOL_A = "ICICIBANK";
const PAIRS_SYMBOL_B = "HDFCBANK";
const PAIRS_STRATEGY_KEY = "pairs_cointegration";

function NoteField({ note, setNote }) {
  return html`
    <div class="field">
      <label>Notes (optional)</label>
      <textarea class="input" rows="2" placeholder="Why this trade?" value=${note}
                onInput=${(e) => setNote(e.target.value)} style=${{ resize: "vertical", fontFamily: "inherit" }} />
    </div>
  `;
}

function SingleTicket({ symbols, symbol, setSymbol, price, prefillSide }) {
  // Initial value only (useState's lazy-init form), not a live-controlled
  // prop: the B/S shortcut PRE-FILLS the ticket, it doesn't lock it -- the
  // user can still freely toggle Buy/Sell afterward, same as any other
  // field on this form. See ticketIntent.js's module comment for the
  // safety boundary (this can only ever set an initial value, never submit).
  const [side, setSide] = React.useState(() => prefillSide || "buy");
  const [orderType, setOrderType] = React.useState("market");
  const [qty, setQty] = React.useState("10");
  const [px, setPx] = React.useState("");
  const [showBracket, setShowBracket] = React.useState(false);
  const [stopLoss, setStopLoss] = React.useState("");
  const [takeProfit, setTakeProfit] = React.useState("");
  const [note, setNote] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [pendingLiveOrder, setPendingLiveOrder] = React.useState(null); // OrderOut | null -- see LiveOrderConfirmModal
  const pxTouchedRef = React.useRef(false);
  const toast = useToast();
  const tradingMode = useTradingMode();
  const isLive = tradingMode === "live";

  React.useEffect(() => {
    if (!pxTouchedRef.current && price) setPx(price.toFixed(2));
  }, [price]);

  async function submit() {
    const qtyNum = parseInt(qty, 10);
    if (!qtyNum || qtyNum <= 0) return toast("Enter a valid quantity", "err");
    if (orderType === "limit" && (!px || Number(px) <= 0)) return toast("Enter a valid price", "err");
    const slNum = showBracket && stopLoss ? Number(stopLoss) : null;
    const tpNum = showBracket && takeProfit ? Number(takeProfit) : null;
    if (showBracket && slNum === null && tpNum === null) return toast("Set a stop-loss and/or take-profit, or turn off Risk Management", "err");

    setSubmitting(true);
    try {
      const order = await api.orders.submit({
        symbol, side, order_type: orderType, qty: qtyNum,
        px: orderType === "limit" ? Number(px) : null,
        stop_loss_px: slNum, take_profit_px: tpNum,
        mode: tradingMode,
      });
      // api.journal.create, not the removed api.dashboard.notes.create --
      // that endpoint was deleted server-side when Journal.js replaced it
      // (see app/routers/journal.py's module docstring); calling it threw
      // here on every note-attached submit, surfacing a false "Order
      // rejected" toast for an order that had, in fact, already gone
      // through. Fixed to also link the note to the order it's actually
      // about, which the old call had no way to do at all.
      if (note.trim()) await api.journal.create({ text: note.trim(), trade_id: order.id });
      if (order.status === "pending_confirmation") {
        setPendingLiveOrder(order);
      } else if (order.filled_qty > 0) {
        toast(`${order.status === "filled" ? "Filled" : "Partially filled"} ${order.filled_qty}/${qtyNum} @ ₹${order.avg_fill_px?.toFixed(2)}`, "ok");
      } else if (orderType === "market") {
        toast("No fill — no liquidity on the other side right now", "err");
      } else {
        toast("Order resting", "ok");
      }
      setNote("");
    } catch (e) {
      toast(e.message || "Order rejected", "err");
    } finally {
      setSubmitting(false);
    }
  }

  return html`
    <div class="panel panel-pad order-ticket-panel">
      <div class="panel-title">Ticket</div>
      <${OrderModeBanner} />

      ${isLive && html`
        <div class="field">
          <label>Search Any Real Stock (Live)</label>
          <${LiveSymbolSearch} onSelect=${setSymbol} />
        </div>
      `}

      <div class="field">
        <label>Symbol</label>
        <select class="input" value=${symbol} onChange=${(e) => setSymbol(e.target.value)}>
          ${symbols.map((s) => html`<option key=${s.symbol} value=${s.symbol}>${s.symbol}</option>`)}
        </select>
      </div>

      <div class="toggle-row" style=${{ marginBottom: "8px" }}>
        <button class=${`btn ${side === "buy" ? "active buy" : ""}`} onClick=${() => setSide("buy")}>Buy <span class="shortcut-hint">B</span></button>
        <button class=${`btn ${side === "sell" ? "active sell" : ""}`} onClick=${() => setSide("sell")}>Sell <span class="shortcut-hint">S</span></button>
      </div>
      <div class="toggle-row" style=${{ marginBottom: "14px" }}>
        <button class=${`btn ${orderType === "limit" ? "active neutral" : ""}`} onClick=${() => setOrderType("limit")}>Limit</button>
        <button class=${`btn ${orderType === "market" ? "active neutral" : ""}`} onClick=${() => setOrderType("market")}>Market</button>
      </div>

      <div class="field">
        <label>Quantity</label>
        <input class="input" type="number" min="1" value=${qty} onInput=${(e) => setQty(e.target.value)} />
      </div>

      ${orderType === "limit" && html`
        <div class="field">
          <label>Price (₹)</label>
          <input class="input" type="number" min="0" step="0.05" value=${px}
                 onFocus=${() => { pxTouchedRef.current = true; }}
                 onInput=${(e) => setPx(e.target.value)} />
        </div>
      `}

      <div class="field" style=${{ marginTop: "4px" }}>
        <label style=${{ display: "flex", alignItems: "center", gap: "7px", cursor: isLive ? "not-allowed" : "pointer", textTransform: "none", letterSpacing: "normal", fontSize: "12px", color: "var(--text-dim)", opacity: isLive ? 0.5 : 1 }}
               title=${isLive ? "Not yet supported for live orders" : undefined}>
          <input type="checkbox" checked=${showBracket} disabled=${isLive} onChange=${(e) => setShowBracket(e.target.checked)} />
          Risk Management (Stop Loss / Take Profit)${isLive ? " — not available in live mode yet" : ""}
        </label>
      </div>
      ${showBracket && !isLive && html`
        <${React.Fragment}>
          <div class="field">
            <label>Stop Loss (₹)</label>
            <input class="input" type="number" min="0" step="0.05" placeholder="optional" value=${stopLoss} onInput=${(e) => setStopLoss(e.target.value)} />
          </div>
          <div class="field">
            <label>Take Profit (₹)</label>
            <input class="input" type="number" min="0" step="0.05" placeholder="optional" value=${takeProfit} onInput=${(e) => setTakeProfit(e.target.value)} />
          </div>
        <//>
      `}

      <${NoteField} note=${note} setNote=${setNote} />

      <button class=${`btn btn-block ${side === "buy" ? "btn-buy" : "btn-sell"}`} disabled=${submitting} onClick=${submit}>
        ${submitting ? "Submitting…" : `Submit ${side === "buy" ? "Buy" : "Sell"}`}
      </button>

      ${pendingLiveOrder && html`
        <${LiveOrderConfirmModal} orders=${[pendingLiveOrder]}
          onDone=${() => setPendingLiveOrder(null)}
          onClose=${() => setPendingLiveOrder(null)} />
      `}
    </div>
  `;
}

function SpreadTicket({ pairData }) {
  const [direction, setDirection] = React.useState("long_spread");
  const [qty, setQty] = React.useState("10");
  const [note, setNote] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [pendingLiveOrders, setPendingLiveOrders] = React.useState(null); // OrderOut[] | null -- see LiveOrderConfirmModal
  const toast = useToast();
  const tradingMode = useTradingMode();
  const isLive = tradingMode === "live";

  const hedgeRatio = pairData?.hedge_ratio ?? null;
  const qtyA = parseInt(qty, 10) || 0;
  const qtyB = hedgeRatio !== null && qtyA > 0 ? Math.max(1, Math.round(qtyA * hedgeRatio)) : null;

  async function submit() {
    if (!qtyA || qtyA <= 0) return toast("Enter a valid quantity", "err");
    if (hedgeRatio === null || qtyB === null) return toast("Hedge ratio isn't ready yet — check the Pairs page", "err");

    const sideA = direction === "long_spread" ? "buy" : "sell";
    const sideB = direction === "long_spread" ? "sell" : "buy";

    setSubmitting(true);
    let orderA = null;
    try {
      // Two independent requests, not one atomic call -- if leg B's
      // request itself fails (as opposed to just resting unfilled, which
      // is normal engine behavior, see the Pairs page's Force Close), leg
      // A may already be live. The catch below must say so explicitly:
      // a generic "order rejected" toast here would hide a real, already-
      // open single-leg position from the user.
      orderA = await api.orders.submit({
        symbol: PAIRS_SYMBOL_A, side: sideA, order_type: "market", qty: qtyA, strategy_key: PAIRS_STRATEGY_KEY,
        mode: tradingMode,
      });
      const orderB = await api.orders.submit({
        symbol: PAIRS_SYMBOL_B, side: sideB, order_type: "market", qty: qtyB, strategy_key: PAIRS_STRATEGY_KEY,
        mode: tradingMode,
      });
      // api.journal.create, not the removed api.dashboard.notes.create --
      // see SingleTicket's identical fix above for why the old call threw
      // on every note-attached submit despite the order(s) already having
      // gone through.
      if (note.trim()) await api.journal.create({ text: note.trim(), trade_id: orderA.id });

      // Live orders never fill here -- both legs land as pending_confirmation
      // with zero broker contact (see LiveOrderConfirmModal's own comment).
      // Confirmed/rejected TOGETHER, one deliberate action for what the
      // user experienced as one spread trade, not two disjoint popups.
      const pending = [orderA, orderB].filter((o) => o.status === "pending_confirmation");
      if (pending.length) {
        setPendingLiveOrders(pending);
      } else {
        toast(`Spread submitted — ${PAIRS_SYMBOL_A} ${orderA.filled_qty}/${qtyA}, ${PAIRS_SYMBOL_B} ${orderB.filled_qty}/${qtyB}`, "ok");
      }
      setNote("");
    } catch (e) {
      if (orderA && orderA.status === "pending_confirmation") {
        // Leg A is sitting un-confirmed, un-dispatched -- surfaced for
        // review/reject specifically, not just left invisible behind an
        // error toast the way a filled leg A already was below.
        setPendingLiveOrders([orderA]);
        toast(`${PAIRS_SYMBOL_B} leg failed before ever submitting: ${e.message || "order rejected"} — ${PAIRS_SYMBOL_A} leg is pending confirmation, review it below before deciding`, "err");
      } else if (orderA) {
        toast(`${PAIRS_SYMBOL_A} leg went through (${orderA.filled_qty}/${qtyA} filled) but ${PAIRS_SYMBOL_B} leg failed: ${e.message || "order rejected"} — check Pairs for a one-sided position and close it manually if needed`, "err");
      } else {
        toast(e.message || "Order rejected", "err");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return html`
    <div class="panel panel-pad order-ticket-panel">
      <div class="panel-title">Ticket</div>
      <${OrderModeBanner} />
      <div class="field">
        <label>Spread</label>
        <div class="input" style=${{ color: "var(--text-dim)" }}>${PAIRS_SYMBOL_A} / ${PAIRS_SYMBOL_B} (fixed — the only validated pair)</div>
      </div>

      <div class="toggle-row" style=${{ marginBottom: "14px" }}>
        <button class=${`btn ${direction === "long_spread" ? "active buy" : ""}`} onClick=${() => setDirection("long_spread")}>Long Spread</button>
        <button class=${`btn ${direction === "short_spread" ? "active sell" : ""}`} onClick=${() => setDirection("short_spread")}>Short Spread</button>
      </div>

      <div class="field">
        <label>Quantity (${PAIRS_SYMBOL_A} leg)</label>
        <input class="input" type="number" min="1" value=${qty} onInput=${(e) => setQty(e.target.value)} />
      </div>

      <div class="row hairline"><span>Current Hedge Ratio (β)</span><span class="mono">${hedgeRatio !== null ? hedgeRatio.toFixed(4) : "—"}</span></div>
      <div class="row hairline">
        <span>${direction === "long_spread" ? "Buy" : "Sell"} ${PAIRS_SYMBOL_A}</span>
        <span class="mono">${qtyA || "—"}</span>
      </div>
      <div class="row" style=${{ marginBottom: "14px" }}>
        <span>${direction === "long_spread" ? "Sell" : "Buy"} ${PAIRS_SYMBOL_B}</span>
        <span class="mono">${qtyB ?? "—"}</span>
      </div>

      <div style=${{ color: "var(--text-faint)", fontSize: "11px", lineHeight: 1.6, marginBottom: "14px" }}>
        Leg B is sized to the current Kalman hedge ratio, not equal to leg A — the same beta-scaled sizing the
        automated strategy uses. Spread positions are risk-managed by z-score (see the Pairs page's Stop Z-Score),
        not a per-leg price stop, so Risk Management fields aren't offered here.
        ${isLive ? " Both legs are independent live orders — you'll confirm them together before either reaches the broker." : ""}
      </div>

      <${NoteField} note=${note} setNote=${setNote} />

      <button class="btn btn-block btn-primary" disabled=${submitting} onClick=${submit}>
        ${submitting ? "Submitting…" : `Submit ${direction === "long_spread" ? "Long Spread" : "Short Spread"}`}
      </button>

      ${pendingLiveOrders && html`
        <${LiveOrderConfirmModal} orders=${pendingLiveOrders}
          onDone=${() => setPendingLiveOrders(null)}
          onClose=${() => setPendingLiveOrders(null)} />
      `}
    </div>
  `;
}

export function ManualTrade() {
  // Read once, at mount -- see ticketIntent.js: a B/S shortcut sets this
  // right before navigating here, and this page is the one-shot consumer.
  const [ticketIntent] = React.useState(() => consumeTicketIntent());
  const [mode, setMode] = React.useState("single");
  const [symbols, setSymbols] = React.useState([]);
  const [symbol, setSymbol] = React.useState(null);
  const [tick, setTick] = React.useState(null);
  const [pairData, setPairData] = React.useState(null);
  const tradingMode = useTradingMode();

  React.useEffect(() => {
    api.symbols().then((rows) => {
      setSymbols(rows);
      if (rows.length) setSymbol(rows[0].symbol);
    });
  }, []);

  // Same real gap Terminal.js's own comment documents (confirmed live,
  // 2026-09-03: TATAMOTORS has zero real Angel One listings, almost
  // certainly the corporate demerger) -- this ticket's own symbol
  // picker needs the same live-mode filter, not just Terminal's.
  const visibleSymbols = tradingMode === "live" ? symbols.filter((s) => s.live_tradable) : symbols;
  // Skipped entirely while in live mode -- `symbol` there can be a real
  // equity picked via the LiveSymbolSearch below, drawn from the full
  // ~2000+ real universe (GET /live/market/equities), not the curated
  // 7-name `symbols` array this effect checks against. Same fix Terminal.js
  // needed for the identical reason -- see that file's own comment.
  React.useEffect(() => {
    if (tradingMode === "live") return;
    if (!symbol || !visibleSymbols.length) return;
    if (!visibleSymbols.some((s) => s.symbol === symbol)) {
      setSymbol(visibleSymbols[0].symbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tradingMode, symbols]);

  React.useEffect(() => {
    if (mode !== "single" || !symbol) return;
    setTick(null);
    const unsub = subscribeMarketForMode(tradingMode, symbol, setTick);
    return unsub;
  }, [mode, symbol, tradingMode]);

  const loadPairData = React.useCallback(() => {
    if (mode !== "spread") return;
    api.pairs.overview().then(setPairData).catch(() => {});
  }, [mode]);
  React.useEffect(() => { loadPairData(); }, [loadPairData]);
  React.useEffect(() => {
    if (mode !== "spread") return;
    const id = setInterval(loadPairData, 5000);
    return () => clearInterval(id);
  }, [mode, loadPairData]);

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Manual Trade</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "14px" }}>
        One order ticket for either a single instrument or a hedge-ratio-scaled spread trade
      </div>

      <div class="toggle-row" style=${{ marginBottom: "16px", maxWidth: "340px" }}>
        <button class=${`btn ${mode === "single" ? "active neutral" : ""}`} onClick=${() => setMode("single")}>Single Symbol</button>
        <button class=${`btn ${mode === "spread" ? "active neutral" : ""}`} onClick=${() => setMode("spread")}>Spread (Pair)</button>
      </div>

      <div class="trade-grid">
        <${ErrorBoundary} label=${mode === "single" ? "Chart" : "Live Spread Stats"}>
          ${mode === "single"
            ? html`
              <div class="panel panel-pad">
                ${symbol && html`
                  <${React.Fragment}>
                    <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                      <span style=${{ fontWeight: 700 }}>${symbol}</span>
                      ${tick?.price && html`<span class="mono" style=${{ color: "var(--text-dim)" }}>${fmtMoney(tick.price)}</span>`}
                    </div>
                    <${CandleChart} symbol=${symbol} price=${tick?.price} height="360px" />
                  <//>
                `}
              </div>
            `
            : html`
              <div class="panel panel-pad">
                <div class="panel-title">Live Spread Stats</div>
                ${!pairData || pairData.warming_up
                  ? html`<div style=${{ color: "var(--text-faint)", padding: "20px 0" }}>Building up enough price history before spread stats are available.</div>`
                  : html`
                    <div style=${{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "12px" }} class="dash-stats">
                      <div class="stat-card">
                        <div class="stat-label">Spread Z-Score</div>
                        <div class="stat-value mono">${pairData.zscore?.toFixed(3) ?? "—"}</div>
                      </div>
                      <div class="stat-card">
                        <div class="stat-label">Kalman Hedge Ratio (β)</div>
                        <div class="stat-value mono">${pairData.hedge_ratio?.toFixed(4) ?? "—"}</div>
                      </div>
                      <div class="stat-card">
                        <div class="stat-label">Cointegration p-value</div>
                        <div class="stat-value mono">${pairData.cointegration_pvalue?.toFixed(4) ?? "—"}</div>
                      </div>
                      <div class="stat-card">
                        <div class="stat-label">Current Position</div>
                        <div class="stat-value mono">${pairData.position === "none" ? "Flat" : pairData.position === "long_spread" ? "Long" : "Short"}</div>
                      </div>
                    </div>
                    <!-- Same real z-gauge the Pairs page's own Overview
                         tab shows (BellCurve, fed by this SAME already-
                         fetched pairData -- no new endpoint) -- this
                         panel used to end at the 4 stat cards above,
                         leaving the ticket's own taller right column
                         towering over a mostly-empty left one. A visual
                         read of exactly where the spread sits relative to
                         entry/stop, not decoration: it's the same real
                         zscore/config values the stat cards already show,
                         just as a shape instead of only numbers. -->
                    <div style=${{ marginTop: "18px" }}>
                      <div class="panel-title" style=${{ marginBottom: "8px" }}>Spread Position (z-gauge)</div>
                      <${BellCurve} zScore=${pairData.zscore ?? null}
                        entryZ=${pairData.zscore !== null && pairData.zscore < 0 ? -pairData.config.entry_z : pairData.config.entry_z}
                        stopZ=${pairData.zscore !== null && pairData.zscore < 0 ? -pairData.config.stop_z : pairData.config.stop_z}
                        height=${160} />
                    </div>
                  `}
              </div>
            `}
        <//>

        <${ErrorBoundary} label="Ticket">
          ${mode === "single"
            ? html`<${SingleTicket} symbols=${visibleSymbols} symbol=${symbol} setSymbol=${setSymbol} price=${tick?.price} prefillSide=${ticketIntent?.side} />`
            : html`<${SpreadTicket} pairData=${pairData} />`}
        <//>
      </div>
    </div>
  `;
}
