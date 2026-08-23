import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useToast } from "../toast.js";

export function OrderEntry({ symbol, price, onOrderPlaced }) {
  const [side, setSide] = React.useState("buy");
  const [orderType, setOrderType] = React.useState("market");
  const [qty, setQty] = React.useState("10");
  const [px, setPx] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const pxTouchedRef = React.useRef(false);
  const toast = useToast();

  // Track the live price by default, same UX as the bourse demo's own
  // order form -- but never fight a visitor who's actively typing.
  React.useEffect(() => {
    if (!pxTouchedRef.current && price) setPx(price.toFixed(2));
  }, [price]);

  async function submit() {
    const qtyNum = parseInt(qty, 10);
    if (!qtyNum || qtyNum <= 0) return toast("Enter a valid quantity", "err");
    if (orderType === "limit" && (!px || Number(px) <= 0)) return toast("Enter a valid price", "err");

    setSubmitting(true);
    try {
      const order = await api.orders.submit({
        symbol, side, order_type: orderType, qty: qtyNum,
        px: orderType === "limit" ? Number(px) : null,
      });
      if (order.filled_qty > 0) {
        toast(`${order.status === "filled" ? "Filled" : "Partially filled"} ${order.filled_qty}/${qtyNum} @ ₹${order.avg_fill_px?.toFixed(2)}`, "ok");
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

      <button class=${`btn btn-block ${side === "buy" ? "btn-buy" : "btn-sell"}`} disabled=${submitting} onClick=${submit}>
        ${submitting ? "Submitting…" : `Submit ${side === "buy" ? "Buy" : "Sell"}`}
      </button>
    </div>
  `;
}
