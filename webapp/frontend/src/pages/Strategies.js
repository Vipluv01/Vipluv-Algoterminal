import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useToast } from "../toast.js";

const STRATEGY_BLURBS = {
  alpha_rsi_ema: "Trades EMA(9)/EMA(21) crossovers, confirmed by RSI recovering from oversold or pulling back from overbought.",
  momentum_macd: "Follows a sustained trend: enters on a MACD histogram flip in the direction price is already moving relative to its EMA(50).",
  mean_reversion_bb: "Fades a single instrument back toward its own recent mean when price pierces a Bollinger Band, confirmed by RSI.",
  pairs_cointegration: "Trades the ICICIBANK/HDFCBANK spread back to equilibrium via Engle-Granger cointegration and a Kalman-filtered hedge ratio — validated methodology from icici_mean_reversion (Sharpe 1.74 backtested).",
};

// Real bug fixed 2026-09-04, found live via a Playwright walkthrough:
// this card used to branch on `!isPairs` alone, so an OPTIONS strategy
// (iron_condor, calendar_spread, ...) fell into the same "editable"
// branch a single-instrument strategy does -- an editable Symbol
// dropdown a user could believe actually retargeted the strategy, and a
// "Single Instrument" badge that was simply wrong for a multi-leg
// options strategy. The backend already silently ignored whatever
// symbol got submitted for options (routers/strategies.py's
// set_allocation), so this was misleading UI, not a broken trade -- but
// still exactly the kind of "looks configurable, isn't" gap worth
// fixing. Branches on strategy.kind directly now, one real case per kind.
function StrategyCard({ strategy, allocation, symbolOptions, onSave }) {
  const isSingleInstrument = strategy.kind === "single_instrument";
  const kindLabel = strategy.kind === "pairs" ? "Pairs" : strategy.kind === "options" ? "Options" : "Single Instrument";
  const [enabled, setEnabled] = React.useState(allocation?.enabled ?? false);
  const [symbol, setSymbol] = React.useState(allocation?.symbol ?? symbolOptions[0]);
  const [weight, setWeight] = React.useState(allocation?.weight ?? 0.5);
  const [saving, setSaving] = React.useState(false);
  const toast = useToast();

  async function save(nextEnabled) {
    setSaving(true);
    try {
      await onSave(strategy.key, { enabled: nextEnabled, symbol: isSingleInstrument ? symbol : null, weight: Number(weight) });
      setEnabled(nextEnabled);
    } catch (e) {
      toast(e.message || "Could not update strategy", "err");
    } finally {
      setSaving(false);
    }
  }

  return html`
    <div class="panel panel-pad fade-in">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "10px" }}>
        <div>
          <div style=${{ fontWeight: 700, fontSize: "14px" }}>${strategy.name}</div>
          <span class=${`badge ${isSingleInstrument ? "badge-off" : "badge-accent"}`} style=${{ marginTop: "6px" }}>${kindLabel}</span>
        </div>
        <span class=${`badge ${enabled ? "badge-live" : "badge-off"}`}>${enabled ? "● Running" : "Off"}</span>
      </div>
      <p style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.5, margin: "0 0 14px" }}>${STRATEGY_BLURBS[strategy.key] || ""}</p>

      ${isSingleInstrument
        ? html`
          <div class="field">
            <label>Symbol</label>
            <select class="input" value=${symbol} onChange=${(e) => setSymbol(e.target.value)} disabled=${enabled}>
              ${symbolOptions.map((s) => html`<option key=${s} value=${s}>${s}</option>`)}
            </select>
          </div>
        `
        : html`
          <div class="field">
            <label>${strategy.kind === "pairs" ? "Pair" : "Underlying"}</label>
            <div class="input" style=${{ color: "var(--text-dim)" }}>
              ${strategy.fixed_underlying} (fixed — ${strategy.kind === "pairs" ? "the only pair with validated cointegration evidence" : "not user-configurable for this strategy"})
            </div>
          </div>
        `}

      <div class="field">
        <label>Weight (informational — portfolio optimizer target)</label>
        <input class="input" type="number" min="0" max="1" step="0.05" value=${weight} onInput=${(e) => setWeight(e.target.value)} />
      </div>

      <button class=${`btn btn-block ${enabled ? "btn-ghost" : "btn-primary"}`} disabled=${saving} onClick=${() => save(!enabled)}>
        ${saving ? "Saving…" : enabled ? "Disable" : "Enable"}
      </button>
    </div>
  `;
}

export function Strategies() {
  const [strategies, setStrategies] = React.useState([]);
  const [allocations, setAllocations] = React.useState([]);
  // Real bug fixed 2026-09-04: the symbol picker was a hardcoded 7-name
  // literal, already stale against app/routers/strategies.py's own
  // server-side validation (body.symbol not in NAMED_INSTRUMENTS), which
  // has generalized to every paper/virtual symbol since app/markets.py's
  // own 2026-09-04 expansion to 22. Fetched here (not hardcoded again)
  // so this picker can never silently fall behind that list a second
  // time -- filtered to real instruments only (!is_derived): a
  // single-instrument strategy can't meaningfully trade a synthetic
  // index the same way a real symbol's own order book can be traded.
  const [symbolOptions, setSymbolOptions] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const toast = useToast();

  const load = React.useCallback(async () => {
    const [s, a, syms] = await Promise.all([api.strategies.list(), api.strategies.allocations(), api.symbols()]);
    setStrategies(s);
    setAllocations(a);
    setSymbolOptions(syms.filter((sym) => !sym.is_derived).map((sym) => sym.symbol));
    setLoading(false);
  }, []);
  React.useEffect(() => { load(); }, [load]);

  async function saveAllocation(key, body) {
    await api.strategies.setAllocation(key, body);
    toast(body.enabled ? "Strategy enabled — evaluating on every market tick" : "Strategy disabled", "ok");
    load();
  }

  const allocByKey = Object.fromEntries(allocations.map((a) => [a.strategy_key, a]));

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Strategies</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>
        Enable a strategy and it evaluates automatically against live market data every tick, submitting real paper orders when it signals.
      </div>

      ${loading
        ? html`<div style=${{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "14px" }}>
            ${[0, 1, 2, 3].map((i) => html`<div key=${i} class="skeleton" style=${{ height: "220px" }} />`)}
          </div>`
        : html`
          <div class="strategies-grid">
            ${strategies.map((s) => html`
              <${StrategyCard} key=${s.key} strategy=${s} allocation=${allocByKey[s.key]} symbolOptions=${symbolOptions} onSave=${saveAllocation} />
            `)}
          </div>
        `}
    </div>
  `;
}
