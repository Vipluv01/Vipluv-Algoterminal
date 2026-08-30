import React from "react";
import { html } from "../html.js";
import { Modal } from "./Modal.js";
import { api } from "../api.js";
import { useToast } from "../toast.js";

const CONFIRM_WORD = "CONFIRM";

// The ONE UI entry point for POST /orders/{id}/confirm -- the sole path
// that actually reaches a real broker (see api.js's own comment, and
// app/routers/orders.py's confirm_live_order). A mode="live" submit only
// ever creates a pending_confirmation row with zero broker contact;
// without this modal existing, that row had no way to ever leave
// pending_confirmation, which was the second half of the 2026-08-30
// "live mode never actually places an order" finding (the first half was
// submissions not carrying mode at all -- see OrderEntry.js/
// ManualTrade.js).
//
// `orders` is an array, not a single order -- a pairs spread ticket
// submits TWO independent orders for what the user experienced as one
// action, and this shows/confirms/rejects both together rather than two
// disjoint popups. A single-symbol ticket just passes an array of one.
export function LiveOrderConfirmModal({ orders, onDone, onClose }) {
  const [typed, setTyped] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const toast = useToast();
  const canConfirm = typed.trim().toUpperCase() === CONFIRM_WORD;

  async function confirmAll() {
    setBusy(true);
    const results = [];
    for (const o of orders) {
      try {
        const confirmed = await api.orders.confirm(o.id);
        results.push({ ok: true, order: confirmed });
      } catch (e) {
        // One leg failing must not be hidden behind the other's success --
        // a spread that only half-confirmed is exactly the kind of
        // one-sided-position risk ManualTrade.js's own SpreadTicket
        // already warns about for the ordinary (non-live) submit path.
        results.push({ ok: false, id: o.id, error: e.message || "Confirm failed" });
      }
    }
    setBusy(false);
    const failed = results.filter((r) => !r.ok);
    if (failed.length) {
      toast(`${failed.length}/${orders.length} leg(s) failed to reach the broker: ${failed.map((f) => `#${f.id} — ${f.error}`).join("; ")}`, "err");
    } else {
      toast(`Sent to broker: ${results.map((r) => `${r.order.symbol} ${r.order.side} ${r.order.qty}`).join(", ")}`, "ok");
    }
    onDone && onDone(results);
  }

  async function rejectAll() {
    setBusy(true);
    try {
      // Cancelling a pending_confirmation order is a pure DB status flip
      // -- it never reached the broker, so there is nothing on Angel
      // One's side to undo (see app/routers/orders.py's cancel_order).
      await Promise.all(orders.map((o) => api.orders.cancel(o.id)));
      toast(orders.length > 1 ? "Order not sent — both legs cancelled before reaching the broker" : "Order not sent — cancelled before reaching the broker", "ok");
    } catch (e) {
      toast(e.message || "Could not cancel", "err");
    } finally {
      setBusy(false);
      onDone && onDone([]);
    }
  }

  return html`
    <${Modal} title="Confirm Live Order" onClose=${onClose} size="sm">
      <p style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6, marginTop: 0 }}>
        This order has <strong style=${{ color: "var(--text)" }}>not</strong> reached your broker yet.
        Review it, then type <strong>${CONFIRM_WORD}</strong> to send it to Angel One — or reject it to
        cancel without the broker ever seeing it.
      </p>
      <div style=${{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "14px" }}>
        ${orders.map((o) => html`
          <div key=${o.id} class="mono" style=${{ display: "flex", gap: "10px", alignItems: "center", padding: "8px 10px", background: "var(--surface-2)", borderRadius: "6px", fontSize: "13px" }}>
            <span class=${o.side === "buy" ? "pos" : "neg"} style=${{ fontWeight: 700, minWidth: "60px" }}>${o.side === "buy" ? "▲ BUY" : "▼ SELL"}</span>
            <span style=${{ flex: 1 }}>${o.qty} ${o.symbol}</span>
            <span style=${{ color: "var(--text-dim)" }}>${o.px != null ? `@ ${o.px}` : "@ market"}</span>
          </div>
        `)}
      </div>
      <div class="field">
        <input class="input" value=${typed} onInput=${(e) => setTyped(e.target.value)} placeholder=${CONFIRM_WORD} autofocus disabled=${busy} />
      </div>
      <div style=${{ display: "flex", gap: "10px", marginTop: "12px" }}>
        <button class="btn btn-ghost" style=${{ flex: 1 }} onClick=${rejectAll} disabled=${busy}>Reject (Don't Send)</button>
        <button class="btn btn-sell" style=${{ flex: 1 }} disabled=${!canConfirm || busy} onClick=${confirmAll}>
          ${busy ? "Sending…" : "Confirm & Send to Broker"}
        </button>
      </div>
    <//>
  `;
}
