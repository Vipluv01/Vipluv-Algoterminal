import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney } from "../format.js";
import { useToast } from "../toast.js";
import { OrderModeBanner } from "./OrderModeBanner.js";
import { LiveOrderConfirmModal } from "./LiveOrderConfirmModal.js";
import { useMode } from "../mode.js";

const QUICK_PCTS = [25, 50, 75, 100];

// Real available cash, mode-aware -- paper/virtual only. Live has no
// account-snapshot endpoint yet (same honest gap AccountPanel.js/
// Accounts.js already carve out for it), so the quick-% row below is
// simply not offered there rather than computed against a number this
// app doesn't actually have for a live account.
function useAvailableCash(mode) {
  const [cash, setCash] = React.useState(null);
  React.useEffect(() => {
    setCash(null);
    if (mode === "live") return;
    let cancelled = false;
    const fetchAccount = mode === "virtual" ? api.virtual.account : api.account;
    fetchAccount().then((acc) => { if (!cancelled) setCash(acc?.cash ?? null); }).catch(() => {});
    return () => { cancelled = true; };
  }, [mode]);
  return cash;
}

// GET /telemetry/latency -- the SAME real, already-measured aggregate
// StatusBar.js's own connection indicator already surfaces (an aggregate
// over recent order submits, n_samples/p50_ms/p99_ms or null before the
// first real submit) -- not a per-order estimate invented for this
// preview.
function useExecutionLatency() {
  const [latency, setLatency] = React.useState(undefined);
  React.useEffect(() => {
    let cancelled = false;
    api.telemetry.latency().then((v) => { if (!cancelled) setLatency(v); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  return latency;
}

// prefill: {side, price, nonce} | undefined -- click-to-trade support.
// `nonce` (a fresh value, e.g. Date.now(), on every click) is what makes a
// SECOND click on a DIFFERENT book level re-apply even when side happens
// to be unchanged from the last click; without it, clicking two bid
// levels in a row would only visibly react to the first one, since React
// only re-runs an effect when its dependencies actually change, and
// side="sell" -> side="sell" isn't a change.
export function OrderEntry({ symbol, price, onOrderPlaced, prefill }) {
  const [side, setSide] = React.useState("buy");
  const [orderType, setOrderType] = React.useState("market");
  const [qty, setQty] = React.useState("10");
  const [px, setPx] = React.useState("");
  const [showBracket, setShowBracket] = React.useState(false);
  const [stopLoss, setStopLoss] = React.useState("");
  const [takeProfit, setTakeProfit] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [pendingLiveOrder, setPendingLiveOrder] = React.useState(null); // OrderOut | null -- see LiveOrderConfirmModal
  const pxTouchedRef = React.useRef(false);
  const toast = useToast();
  const tradingMode = useMode();
  const isLive = tradingMode === "live";
  const availableCash = useAvailableCash(tradingMode);
  const executionLatency = useExecutionLatency();

  // The reference price a quick-% button or the preview below sizes
  // against -- the order's own limit price once one's been typed,
  // otherwise the live market price. Neither is a placeholder: both are
  // real numbers already available to this form.
  const refPrice = orderType === "limit" && Number(px) > 0 ? Number(px) : price;

  function applyQuickPct(pct) {
    if (!availableCash || !refPrice) return;
    const qtyNum = Math.max(1, Math.floor((availableCash * (pct / 100)) / refPrice));
    setQty(String(qtyNum));
  }

  // Track the live price by default, same UX as the bourse demo's own
  // order form -- but never fight a visitor who's actively typing.
  React.useEffect(() => {
    if (!pxTouchedRef.current && price) setPx(price.toFixed(2));
  }, [price]);

  // A click on a book level PRE-FILLS the ticket -- side, order type
  // (limit, at that exact price), and price -- and stops there. It never
  // calls submit(): reaching the order still requires the same explicit
  // Submit click as typing every field in by hand would.
  React.useEffect(() => {
    if (!prefill) return;
    setSide(prefill.side);
    setOrderType("limit");
    setPx(prefill.price.toFixed(2));
    pxTouchedRef.current = true; // stop the live-price effect above from immediately overwriting this
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

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
      // Live orders never fill here -- a mode="live" submit only ever
      // creates a pending_confirmation row with zero broker contact (see
      // app/routers/orders.py's _submit_live_order docstring). This is
      // the one place that hands off to LiveOrderConfirmModal, the sole
      // UI path to POST /orders/{id}/confirm, which is the sole path
      // that actually reaches Angel One.
      if (order.status === "pending_confirmation") {
        setPendingLiveOrder(order);
      } else if (order.filled_qty > 0) {
        const bracketNote = (slNum || tpNum) ? " · bracket attached" : "";
        toast(`${order.status === "filled" ? "Filled" : "Partially filled"} ${order.filled_qty}/${qtyNum} @ ₹${order.avg_fill_px?.toFixed(2)}${bracketNote}`, "ok");
      } else if (orderType === "market") {
        toast("No fill — no liquidity on the other side right now", "err");
      } else {
        toast("Order resting", "ok");
      }
      onOrderPlaced && onOrderPlaced();
    } catch (e) {
      toast(e.message || "Order rejected", "err");
    } finally {
      setSubmitting(false);
    }
  }

  return html`
    <div class="panel panel-pad order-ticket-panel">
      <div class="panel-title">Trade</div>
      <${OrderModeBanner} />

      <div class="toggle-row" style=${{ marginBottom: "8px" }}>
        <button class=${`btn ${side === "buy" ? "active buy" : ""}`} onClick=${() => setSide("buy")}>Buy</button>
        <button class=${`btn ${side === "sell" ? "active sell" : ""}`} onClick=${() => setSide("sell")}>Sell</button>
      </div>
      <div class="toggle-row" style=${{ marginBottom: "14px" }}>
        <button class=${`btn ${orderType === "limit" ? "active neutral" : ""}`} onClick=${() => setOrderType("limit")}>Limit</button>
        <button class=${`btn ${orderType === "market" ? "active neutral" : ""}`} onClick=${() => setOrderType("market")}>Market</button>
      </div>

      <div class="field">
        <label>Quantity</label>
        <input class="input" type="number" min="1" value=${qty} onInput=${(e) => setQty(e.target.value)} />
      </div>

      ${!isLive && html`
        <div class="quick-pct-row" title=${availableCash == null ? "Loading available cash…" : undefined}>
          ${QUICK_PCTS.map((pct) => html`
            <button key=${pct} class="btn btn-sm btn-ghost" disabled=${!availableCash || !refPrice}
                    onClick=${() => applyQuickPct(pct)}>${pct}%</button>
          `)}
        </div>
      `}

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

      <div class="order-preview">
        <div class="order-preview-row">
          <span>Est. order value ${isLive ? "" : "/ margin (cash account)"}</span>
          <span class="mono">${refPrice && Number(qty) ? fmtMoney(refPrice * Number(qty)) : "—"}</span>
        </div>
        <div class="order-preview-row">
          <span>Est. execution latency (p50)</span>
          <span class="mono">${executionLatency === undefined ? "…" : executionLatency ? `${executionLatency.p50_ms.toFixed(2)}ms` : "—"}</span>
        </div>
      </div>

      <button class=${`btn btn-block ${side === "buy" ? "btn-buy" : "btn-sell"}`} disabled=${submitting} onClick=${submit}>
        ${submitting ? "Submitting…" : `Submit ${side === "buy" ? "Buy" : "Sell"}`}
      </button>

      ${pendingLiveOrder && html`
        <${LiveOrderConfirmModal} orders=${[pendingLiveOrder]}
          onDone=${() => { setPendingLiveOrder(null); onOrderPlaced && onOrderPlaced(); }}
          onClose=${() => setPendingLiveOrder(null)} />
      `}
    </div>
  `;
}
