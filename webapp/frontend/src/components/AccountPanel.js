import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, fmtNum, pnlClass } from "../format.js";
import { LineChart } from "./LineChart.js";
import { useMode } from "../mode.js";

const TABS = ["Portfolio", "Open Orders", "Brackets", "All Orders"];

// paper -> GET /account + /account/equity-curve, virtual -> the /virtual
// equivalents -- same response shapes either way (AccountOut's cash/
// total_value/total_unrealized_pnl/positions, EquityPointOut's `equity`),
// mirroring Accounts.js's own useAccountForMode. Live has no account-
// snapshot endpoint yet -- resolves to [null, []] so the Portfolio tab
// below can render the same honest "not available yet" note Accounts.js
// already uses, rather than a confusing wall of dashes with no
// explanation. This component was the actual bug found on 2026-08-30 (the
// user's "live mode shows demo trades" report): it called api.account()/
// api.equityCurve() unconditionally regardless of mode, so switching to
// Live silently kept showing paper's numbers on the main trading screen.
function accountAndCurveForMode(mode) {
  if (mode === "virtual") return Promise.all([api.virtual.account(), api.virtual.equityCurve()]);
  if (mode === "live") return Promise.resolve([null, []]);
  return Promise.all([api.account(), api.equityCurve()]);
}

export function AccountPanel({ refreshKey }) {
  const [tab, setTab] = React.useState("Portfolio");
  const [account, setAccount] = React.useState(null);
  const [orders, setOrders] = React.useState([]);
  const [brackets, setBrackets] = React.useState([]);
  const [equityCurve, setEquityCurve] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const mode = useMode();

  // Orders/brackets DO have real per-mode data even in live mode (unlike
  // the account snapshot above) -- GET /orders and /orders/brackets both
  // support mode=paper|virtual|live already, so Open Orders/Brackets/All
  // Orders stay real and correctly filtered under every mode; only the
  // Portfolio tab needs the "not available yet" carve-out.
  const load = React.useCallback(async () => {
    try {
      const [[acc, curve], ords, brs] = await Promise.all([
        accountAndCurveForMode(mode),
        api.orders.list(mode),
        api.orders.brackets.list(mode),
      ]);
      setAccount(acc);
      setEquityCurve(curve);
      setOrders(ords);
      setBrackets(brs);
    } finally {
      setLoading(false);
    }
  }, [mode]);

  // refreshKey (bumped by Terminal.js right after an order/close action)
  // still refetches IMMEDIATELY -- this interval is only the background
  // safety net for activity this tab didn't itself cause (an automated
  // strategy filling, a bracket triggering). 10s, not 4s: GET /account
  // walks this user's FULL paper order history every call (confirmed
  // live, 2026-09-04: a long-running paper account with 107K+ orders
  // made this a real, measurable cost, not a free local read) -- a
  // background poll doesn't need to pay that as often as a genuinely
  // interactive action does.
  React.useEffect(() => { load(); }, [load, refreshKey]);
  React.useEffect(() => {
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load]);

  async function cancel(id) {
    try { await api.orders.cancel(id); load(); } catch { /* surfaced via order row still showing submitted */ }
  }

  async function cancelBracket(id) {
    try { await api.orders.brackets.cancel(id); load(); } catch { /* surfaced via bracket row still showing active */ }
  }

  const openOrders = orders.filter((o) => o.status === "submitted" || o.status === "partially_filled");

  // A market order in the OPPOSITE direction, sized to exactly the open
  // qty -- the same manual action a trader would take to flatten a
  // position, not a new order-type/endpoint. Live is never reachable
  // here (this whole tab renders the "not available yet" note for live,
  // see below), so this only ever submits paper/virtual.
  async function closePosition(symbol, qty) {
    try {
      await api.orders.submit({ symbol, side: qty > 0 ? "sell" : "buy", order_type: "market", qty: Math.abs(qty), mode });
      load();
    } catch { /* surfaced via the position still showing open on next load */ }
  }

  async function cancelAllOpenOrders() {
    await Promise.all(openOrders.map((o) => api.orders.cancel(o.id).catch(() => {})));
    load();
  }

  return html`
    <div class="panel panel-pad">
      <div class="tabs">
        ${TABS.map((t) => html`
          <div key=${t} class=${`tab ${tab === t ? "active" : ""}`} onClick=${() => setTab(t)}>${t}</div>
        `)}
      </div>

      ${loading && html`<div class="skeleton" style=${{ height: "80px" }} />`}

      ${!loading && tab === "Portfolio" && mode === "live" && html`
        <div style=${{ color: "var(--text-faint)", fontSize: "12px", padding: "12px 0" }}>
          Live account snapshots aren't available yet — check back once this phase's broker integration is complete.
        </div>
      `}

      ${!loading && tab === "Portfolio" && mode !== "live" && html`
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
                <div class="table-row positions-row hairline" style=${{ color: "var(--text-faint)", fontSize: "10.5px", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  <span>Symbol</span><span>Qty</span><span>Avg Entry</span><span>Unrealized</span><span></span>
                </div>
                ${account.positions.map((p) => {
                  // Real % return -- unrealized P&L over the position's
                  // own cost basis (abs(qty) * avg_entry_px), the same
                  // math app/routers/portfolio.py's attribution already
                  // uses for a position's own return, not a new formula
                  // invented for this badge.
                  const costBasis = Math.abs(p.qty) * p.avg_entry_px;
                  const pctReturn = costBasis > 0 ? (p.unrealized_pnl / costBasis) * 100 : null;
                  return html`
                  <div key=${p.symbol} class="table-row positions-row hairline">
                    <span style=${{ fontWeight: 600 }}>${p.symbol}</span>
                    <span class=${`mono ${pnlClass(p.qty)}`}>${p.qty > 0 ? "+" : ""}${fmtNum(p.qty)}</span>
                    <span class="mono">${fmtMoney(p.avg_entry_px)}</span>
                    <span style=${{ display: "flex", justifyContent: "flex-end" }}>
                      <span class=${`pnl-badge ${pnlClass(p.unrealized_pnl)}`}>
                        <span>${fmtMoney(p.unrealized_pnl)}</span>
                        ${pctReturn != null && html`<span class="pnl-badge-pct">${pctReturn >= 0 ? "+" : ""}${pctReturn.toFixed(2)}%</span>`}
                      </span>
                    </span>
                    <span><button class="btn btn-sm btn-ghost" onClick=${() => closePosition(p.symbol, p.qty)}>Close</button></span>
                  </div>
                `;
                })}
              </div>
            `}
        <//>
      `}

      ${!loading && tab === "Open Orders" && html`
        ${!openOrders.length
          ? html`<div style=${{ color: "var(--text-faint)", padding: "12px 0" }}>No open orders</div>`
          : html`
            <${React.Fragment}>
              <div style=${{ display: "flex", justifyContent: "flex-end", marginBottom: "8px" }}>
                <button class="btn btn-sm btn-ghost" onClick=${cancelAllOpenOrders}>Cancel All (${openOrders.length})</button>
              </div>
              ${openOrders.map((o) => html`
                <div key=${o.id} class="table-row hairline">
                  <span class=${o.side === "buy" ? "pos" : "neg"}>${o.side === "buy" ? "▲" : "▼"} ${o.symbol}</span>
                  <span class="mono">${o.order_type === "limit" ? fmtMoney(o.px) : "MKT"}</span>
                  <span class="mono">${o.qty}</span>
                  <span><button class="btn btn-sm btn-ghost" onClick=${() => cancel(o.id)}>cancel</button></span>
                </div>
              `)}
            <//>
          `}
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
