import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { fmtMoney } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";
import { useToast } from "../toast.js";

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

function SingleTicket({ symbols, symbol, setSymbol, price }) {
  const [side, setSide] = React.useState("buy");
  const [orderType, setOrderType] = React.useState("market");
  const [qty, setQty] = React.useState("10");
  const [px, setPx] = React.useState("");
  const [showBracket, setShowBracket] = React.useState(false);
  const [stopLoss, setStopLoss] = React.useState("");
  const [takeProfit, setTakeProfit] = React.useState("");
  const [note, setNote] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const pxTouchedRef = React.useRef(false);
  const toast = useToast();

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
      });
      if (note.trim()) await api.dashboard.notes.create(note.trim());
      if (order.filled_qty > 0) {
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
    <div class="panel panel-pad">
      <div class="panel-title">Ticket</div>

      <div class="field">
        <label>Symbol</label>
        <select class="input" value=${symbol} onChange=${(e) => setSymbol(e.target.value)}>
          ${symbols.map((s) => html`<option key=${s.symbol} value=${s.symbol}>${s.symbol}</option>`)}
        </select>
      </div>

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

      <${NoteField} note=${note} setNote=${setNote} />

      <button class=${`btn btn-block ${side === "buy" ? "btn-buy" : "btn-sell"}`} disabled=${submitting} onClick=${submit}>
        ${submitting ? "Submitting…" : `Submit ${side === "buy" ? "Buy" : "Sell"}`}
      </button>
    </div>
  `;
}

function SpreadTicket({ pairData }) {
  const [direction, setDirection] = React.useState("long_spread");
  const [qty, setQty] = React.useState("10");
  const [note, setNote] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const toast = useToast();

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
      });
      const orderB = await api.orders.submit({
        symbol: PAIRS_SYMBOL_B, side: sideB, order_type: "market", qty: qtyB, strategy_key: PAIRS_STRATEGY_KEY,
      });
      if (note.trim()) await api.dashboard.notes.create(note.trim());
      toast(`Spread submitted — ${PAIRS_SYMBOL_A} ${orderA.filled_qty}/${qtyA}, ${PAIRS_SYMBOL_B} ${orderB.filled_qty}/${qtyB}`, "ok");
      setNote("");
    } catch (e) {
      if (orderA) {
        toast(`${PAIRS_SYMBOL_A} leg went through (${orderA.filled_qty}/${qtyA} filled) but ${PAIRS_SYMBOL_B} leg failed: ${e.message || "order rejected"} — check Pairs for a one-sided position and close it manually if needed`, "err");
      } else {
        toast(e.message || "Order rejected", "err");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return html`
    <div class="panel panel-pad">
      <div class="panel-title">Ticket</div>
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
      </div>

      <${NoteField} note=${note} setNote=${setNote} />

      <button class="btn btn-block btn-primary" disabled=${submitting} onClick=${submit}>
        ${submitting ? "Submitting…" : `Submit ${direction === "long_spread" ? "Long Spread" : "Short Spread"}`}
      </button>
    </div>
  `;
}

export function ManualTrade() {
  const [mode, setMode] = React.useState("single");
  const [symbols, setSymbols] = React.useState([]);
  const [symbol, setSymbol] = React.useState(null);
  const [tick, setTick] = React.useState(null);
  const [pairData, setPairData] = React.useState(null);

  React.useEffect(() => {
    api.symbols().then((rows) => {
      setSymbols(rows);
      if (rows.length) setSymbol(rows[0].symbol);
    });
  }, []);

  React.useEffect(() => {
    if (mode !== "single" || !symbol) return;
    setTick(null);
    const unsub = subscribeMarket(symbol, setTick);
    return unsub;
  }, [mode, symbol]);

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
                `}
            </div>
          `}

        ${mode === "single"
          ? html`<${SingleTicket} symbols=${symbols} symbol=${symbol} setSymbol=${setSymbol} price=${tick?.price} />`
          : html`<${SpreadTicket} pairData=${pairData} />`}
      </div>
    </div>
  `;
}
