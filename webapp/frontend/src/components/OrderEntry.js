import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useToast } from "../toast.js";

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
  const pxTouchedRef = React.useRef(false);
  const toast = useToast();

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
      });
      if (order.filled_qty > 0) {
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
    <div class="panel panel-pad">
      <div class="panel-title">Trade</div>

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

      ${orderType === "limit" && html`
        <div class="field">
          <label>Price (₹)</label>
          <input class="input" type="number" min="0" step="0.05" value=${px}
                 onFocus=${() => { pxTouchedRef.current = true; }}
                 onInput=${(e) => setPx(e.target.value)} />
        </div>
      `}

      <div class="field" style=${{ marginTop: "4px" }}>
        <label style=${{ display: "flex", alignItems: "center", gap: "7px", cursor: "pointer", textTransform: "none", letterSpacing: "normal", fontSize: "12px", color: "var(--text-dim)" }}>
          <input type="checkbox" checked=${showBracket} onChange=${(e) => setShowBracket(e.target.checked)} />
          Risk Management (Stop Loss / Take Profit)
        </label>
      </div>
      ${showBracket && html`
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

      <button class=${`btn btn-block ${side === "buy" ? "btn-buy" : "btn-sell"}`} disabled=${submitting} onClick=${submit}>
        ${submitting ? "Submitting…" : `Submit ${side === "buy" ? "Buy" : "Sell"}`}
      </button>
    </div>
  `;
}
