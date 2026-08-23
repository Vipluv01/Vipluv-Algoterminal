import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, fmtNum, pnlClass } from "../format.js";
import { LineChart } from "./LineChart.js";

const TABS = ["Portfolio", "Open Orders", "Brackets", "All Orders"];

export function AccountPanel({ refreshKey }) {
  const [tab, setTab] = React.useState("Portfolio");
  const [account, setAccount] = React.useState(null);
  const [orders, setOrders] = React.useState([]);
  const [brackets, setBrackets] = React.useState([]);
  const [equityCurve, setEquityCurve] = React.useState([]);
  const [loading, setLoading] = React.useState(true);

  const load = React.useCallback(async () => {
    try {
      const [acc, ords, brs, curve] = await Promise.all([api.account(), api.orders.list(), api.orders.brackets.list(), api.equityCurve()]);
      setAccount(acc);
      setOrders(ords);
      setBrackets(brs);
      setEquityCurve(curve);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { load(); }, [load, refreshKey]);
  React.useEffect(() => {
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [load]);

  async function cancel(id) {
    try { await api.orders.cancel(id); load(); } catch { /* surfaced via order row still showing submitted */ }
  }

  async function cancelBracket(id) {
    try { await api.orders.brackets.cancel(id); load(); } catch { /* surfaced via bracket row still showing active */ }
  }

  const openOrders = orders.filter((o) => o.status === "submitted" || o.status === "partially_filled");

  return html`
    <div class="panel panel-pad">
      <div class="tabs">
        ${TABS.map((t) => html`
          <div key=${t} class=${`tab ${tab === t ? "active" : ""}`} onClick=${() => setTab(t)}>${t}</div>
        `)}
      </div>

      ${loading && html`<div class="skeleton" style=${{ height: "80px" }} />`}

      ${!loading && tab === "Portfolio" && html`
        <${React.Fragment}>
          <div style=${{ display: "flex", flexWrap: "wrap", gap: "16px 24px", marginBottom: "14px" }}>
            <div>
              <div class="stat-label">Cash</div>
              <div class="mono" style=${{ fontWeight: 600 }}>${fmtMoney(account?.cash)}</div>
            </div>
            <div>
              <div class="stat-label">Total Value</div>
              <div class="mono" style=${{ fontWeight: 600 }}>${fmtMoney(account?.total_value)}</div>
            </div>
            <div>
              <div class="stat-label">Unrealized P&L</div>
              <div class=${`mono ${pnlClass(account?.total_unrealized_pnl)}`} style=${{ fontWeight: 600 }}>${fmtMoney(account?.total_unrealized_pnl)}</div>
            </div>
          </div>

          ${equityCurve.length >= 2 && html`
            <div style=${{ marginBottom: "16px" }}>
              <div class="stat-label" style=${{ marginBottom: "6px" }}>Equity Curve (realized)</div>
              <${LineChart} series=${equityCurve.map((p) => p.equity)}
                color=${equityCurve[equityCurve.length - 1].equity >= equityCurve[0].equity ? "var(--bid-bright)" : "var(--ask-bright)"}
                height=${100} />
            </div>
          `}

          ${!account?.positions?.length
            ? html`<div style=${{ color: "var(--text-faint)", padding: "12px 0" }}>No open positions</div>`
            : html`
              <div>
                <div class="table-row hairline" style=${{ color: "var(--text-faint)", fontSize: "10.5px", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  <span>Symbol</span><span>Qty</span><span>Avg Entry</span><span>Unrealized</span>
                </div>
                ${account.positions.map((p) => html`
                  <div key=${p.symbol} class="table-row hairline">
                    <span style=${{ fontWeight: 600 }}>${p.symbol}</span>
                    <span class=${`mono ${pnlClass(p.qty)}`}>${p.qty > 0 ? "+" : ""}${fmtNum(p.qty)}</span>
                    <span class="mono">${fmtMoney(p.avg_entry_px)}</span>
                    <span class=${`mono ${pnlClass(p.unrealized_pnl)}`}>${fmtMoney(p.unrealized_pnl)}</span>
                  </div>
                `)}
              </div>
            `}
        <//>
      `}

      ${!loading && tab === "Open Orders" && html`
        ${!openOrders.length
          ? html`<div style=${{ color: "var(--text-faint)", padding: "12px 0" }}>No open orders</div>`
          : openOrders.map((o) => html`
            <div key=${o.id} class="table-row hairline">
              <span class=${o.side === "buy" ? "pos" : "neg"}>${o.side === "buy" ? "▲" : "▼"} ${o.symbol}</span>
              <span class="mono">${o.order_type === "limit" ? fmtMoney(o.px) : "MKT"}</span>
              <span class="mono">${o.qty}</span>
              <span><button class="btn btn-sm btn-ghost" onClick=${() => cancel(o.id)}>cancel</button></span>
            </div>
          `)}
      `}

      ${!loading && tab === "Brackets" && html`
        ${!brackets.length
          ? html`<div style=${{ color: "var(--text-faint)", padding: "12px 0" }}>No active stop-loss / take-profit brackets</div>`
          : brackets.map((b) => html`
            <div key=${b.id} class="table-row hairline">
              <span class=${b.entry_side === "buy" ? "pos" : "neg"}>${b.entry_side === "buy" ? "▲" : "▼"} ${b.symbol}</span>
              <span class="mono neg">${b.stop_loss_px ? fmtMoney(b.stop_loss_px) : "—"}</span>
              <span class="mono pos">${b.take_profit_px ? fmtMoney(b.take_profit_px) : "—"}</span>
              <span><button class="btn btn-sm btn-ghost" onClick=${() => cancelBracket(b.id)}>cancel</button></span>
            </div>
          `)}
      `}

      ${!loading && tab === "All Orders" && html`
        ${!orders.length
          ? html`<div style=${{ color: "var(--text-faint)", padding: "12px 0" }}>No orders yet</div>`
          : orders.slice(0, 30).map((o) => html`
            <div key=${o.id} class="table-row hairline">
              <span class=${o.side === "buy" ? "pos" : "neg"}>${o.side === "buy" ? "▲" : "▼"} ${o.symbol}</span>
              <span class="mono">${o.filled_qty}/${o.qty}</span>
              <span class="mono">${o.avg_fill_px ? fmtMoney(o.avg_fill_px) : "—"}</span>
              <span><span class="badge badge-off">${o.status}</span></span>
            </div>
          `)}
      `}
    </div>
  `;
}
