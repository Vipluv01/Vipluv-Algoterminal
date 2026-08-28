import React from "react";
import { html } from "../html.js";
import { api, subscribeMarket } from "../api.js";
import { fmtPct, pct, inr, dash } from "../format.js";
import { useToast } from "../toast.js";
import { refreshRiskStatus } from "../riskStatus.js";

const KELLY_MIN = 0.1, KELLY_MAX = 1.0, KELLY_STEP = 0.05;
const REFERENCE_SYMBOL = "ICICIBANK"; // matches CANONICAL_SYMBOL elsewhere in this codebase

// Mirrors app/position_sizing.py's kelly_fraction() EXACTLY -- same
// classic win-rate/win-loss-ratio Kelly form, same clip-to-zero-not-
// negative behavior -- so the live rupee preview below reflects the real
// backend formula, not an approximation. win_rate/avg_win/avg_loss come
// from GET /dashboard/stats, which is real per-account trade history
// (app/dashboard_stats.py's compute_trade_stats), not a guess.
function kellyFraction(winRate, avgWin, avgLoss) {
  if (winRate === null || winRate === undefined || avgWin === null || avgWin === undefined || avgLoss === null || avgLoss === undefined) return null;
  if (avgWin <= 0 || avgLoss <= 0) return null;
  const b = avgWin / avgLoss;
  const p = winRate;
  const q = 1 - p;
  return Math.max(0, p - q / b);
}

function KellySizer({ risk, onRiskChange }) {
  const [slider, setSlider] = React.useState(risk.kelly_multiplier);
  const [saving, setSaving] = React.useState(false);
  const [dashStats, setDashStats] = React.useState(null);
  const [price, setPrice] = React.useState(null);
  const [accountValue, setAccountValue] = React.useState(null);
  // A silently-swallowed fetch failure here used to be indistinguishable
  // from "genuinely not enough trade history yet" (both left dashStats/
  // accountValue null) -- this account's own "Needs real trade history"
  // copy is a specific, honest claim, and showing it when the real cause
  // was a network failure would be the surprising-number-with-no-reason
  // problem this app is otherwise careful to avoid.
  const [fetchError, setFetchError] = React.useState(null);
  const toast = useToast();

  React.useEffect(() => {
    api.dashboard.stats().then(setDashStats).catch((e) => setFetchError(e.message || "Could not load trade history"));
    api.account().then((a) => setAccountValue(a.total_value)).catch((e) => setFetchError(e.message || "Could not load account value"));
  }, []);
  React.useEffect(() => subscribeMarket(REFERENCE_SYMBOL, (tick) => setPrice(tick.price)), []);

  const fStar = dashStats ? kellyFraction(dashStats.win_rate, dashStats.avg_win, dashStats.avg_loss) : null;
  const haveRealInputs = fStar !== null && accountValue !== null;
  const applied = haveRealInputs ? Math.min(fStar * slider, risk.max_position_fraction) : null;
  const positionValue = haveRealInputs ? accountValue * applied : null;
  const qty = positionValue !== null && price ? Math.floor(positionValue / price) : null;

  async function commit(next) {
    setSaving(true);
    try {
      const updated = await api.risk.update({ kelly_multiplier: next });
      onRiskChange(updated);
      await refreshRiskStatus();
    } catch (e) {
      toast(e.message || "Could not save the Kelly multiplier", "err");
    } finally {
      setSaving(false);
    }
  }

  return html`
    <div class="panel panel-pad" style=${{ marginBottom: "18px" }}>
      <div class="panel-title">Fractional Kelly Sizing</div>
      <div style=${{ display: "flex", alignItems: "center", gap: "14px", marginBottom: "10px" }}>
        <input type="range" min=${KELLY_MIN} max=${KELLY_MAX} step=${KELLY_STEP} value=${slider}
               onInput=${(e) => setSlider(Number(e.target.value))}
               onChange=${(e) => commit(Number(e.target.value))}
               style=${{ flex: 1 }} disabled=${saving} />
        <span class="mono" style=${{ fontWeight: 700, fontSize: "16px", minWidth: "48px", textAlign: "right" }}>${slider.toFixed(2)}×</span>
      </div>
      <div style=${{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px" }} class="dash-stats">
        <div class="stat-card">
          <div class="stat-label">Resulting Position Size</div>
          <div class="stat-value mono">${positionValue !== null ? inr(positionValue, { decimals: 0 }) : dash()}</div>
          <div class="stat-sub">
            ${haveRealInputs
              ? `${qty !== null ? qty + " shares @ current " + REFERENCE_SYMBOL + " price" : "waiting for a live price"}`
              : fetchError
                ? html`Could not load: ${fetchError}`
                : "Needs real trade history (win rate + avg win/loss) — not enough closed trades yet"}
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Full Kelly (f*)</div>
          <div class="stat-value mono">${fStar !== null ? fStar.toFixed(3) : dash()}</div>
          <div class="stat-sub">from this account's real win rate / avg win / avg loss</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Applied Fraction</div>
          <div class="stat-value mono">${applied !== null ? applied.toFixed(3) : dash()}</div>
          <div class="stat-sub">min(f* × ${slider.toFixed(2)}, ${fmtPct(risk.max_position_fraction)} ceiling)</div>
        </div>
      </div>
      ${haveRealInputs && html`
        <div style=${{ marginTop: "14px", padding: "10px 12px", background: "var(--surface-2)", borderRadius: "8px", fontSize: "11.5px", color: "var(--text-dim)", lineHeight: 1.6 }}>
          ${fStar === 0
            ? html`<strong style=${{ color: "var(--text)" }}>Zero isn't a broken calculation — it's the answer.</strong> `
            : ""}
          Computed from this account's real trade history: ${pct(dashStats.win_rate)} win rate,
          ${inr(dashStats.avg_win, { decimals: 2 })} avg win vs ${inr(dashStats.avg_loss, { decimals: 2 })} avg loss
          across ${dashStats.n_trades} trades.${" "}
          ${fStar === 0
            ? "At that win/loss ratio the edge is negative, so Kelly correctly recommends staking nothing — this changes if the win rate or win/loss ratio improves, not by moving the slider."
            : ""}
        </div>
      `}
    </div>
  `;
}

function StatCard({ label, value, sub }) {
  return html`
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class="stat-value mono">${value}</div>
      ${sub && html`<div class="stat-sub">${sub}</div>`}
    </div>
  `;
}

function WeightBar({ label, weight, rank }) {
  return html`
    <div style=${{ marginBottom: "14px" }}>
      <div style=${{ display: "flex", justifyContent: "space-between", marginBottom: "6px", fontSize: "12.5px" }}>
        <span style=${{ fontWeight: 600 }}>${label}</span>
        <span class="mono">${fmtPct(weight)}</span>
      </div>
      <div style=${{ height: "8px", background: "var(--surface-2)", borderRadius: "5px", overflow: "hidden" }}>
        <div style=${{
          width: `${Math.max(weight * 100, 1)}%`, height: "100%", borderRadius: "5px",
          background: rank === 0 ? "linear-gradient(90deg, var(--accent), var(--accent-bright))" : "var(--violet)",
        }} />
      </div>
    </div>
  `;
}

export function Optimizer() {
  const [data, setData] = React.useState(null);
  const [strategies, setStrategies] = React.useState([]);
  const [risk, setRisk] = React.useState(null);
  const [error, setError] = React.useState(null);

  // No .catch() here used to mean any ONE of these three requests failing
  // (a transient network blip, or GET /optimizer being genuinely slow under
  // concurrent-session load -- it recomputes a real Markowitz optimization
  // over this account's full accumulated daily P&L on every call, so it
  // only gets slower as trade history grows) rejected the whole Promise.all
  // silently: `data` stayed null forever, which this component renders as
  // an eternal loading skeleton with no error and no way to retry. Fixed
  // to surface what failed and offer Retry, the same pattern every other
  // screen in this app uses -- a stuck skeleton reads as "still loading"
  // to a viewer forever, not as the failure it actually is.
  const load = React.useCallback(() => {
    setError(null);
    Promise.all([api.optimizer(), api.strategies.list(), api.risk.get()])
      .then(([opt, strats, r]) => {
        setData(opt);
        setStrategies(strats);
        setRisk(r);
      })
      .catch((e) => setError(e.message || "Could not load the optimizer"));
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const nameByKey = Object.fromEntries(strategies.map((s) => [s.key, s.name]));

  const ranked = data && !data.insufficient_history
    ? data.strategy_keys
        .map((key, i) => ({ key, weight: data.weights[i] }))
        .sort((a, b) => b.weight - a.weight)
    : [];

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Optimizer</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>
        Max-Sharpe allocation across every strategy, computed from this account's own real paper-trading P&L — not a synthetic backtest.
      </div>

      ${risk && html`<${KellySizer} risk=${risk} onRiskChange=${setRisk} />`}

      ${error
        ? html`
          <div class="error-state">
            <div>
              <div class="error-state-title">Could not load the optimizer</div>
              <div class="error-state-detail">${error}</div>
            </div>
            <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
          </div>
        `
        : !data
        ? html`<div class="skeleton" style=${{ height: "220px" }} />`
        : data.insufficient_history
          ? html`
            <div class="panel panel-pad" style=${{ textAlign: "center", color: "var(--text-faint)", padding: "40px" }}>
              Not enough real trade history yet — a max-Sharpe allocation needs realized P&L from at least 2 strategies
              across ${data.min_trading_days_required}+ distinct trading days before the estimate means anything.
              Enable a couple of strategies on the Strategies page and let them trade for a while.
            </div>
          `
          : html`
            <div class="dash-grid">
              <div class="panel panel-pad">
                <div class="panel-title">Suggested Allocation</div>
                ${ranked.map((r, i) => html`
                  <${WeightBar} key=${r.key} label=${nameByKey[r.key] || r.key} weight=${r.weight} rank=${i} />
                `)}
              </div>

              <div>
                <div style=${{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "14px" }} class="dash-stats">
                  <${StatCard} label="Expected Return" value=${data.expected_return !== null ? fmtPct(data.expected_return) : "—"} sub="annualized" />
                  <${StatCard} label="Expected Volatility" value=${data.expected_volatility !== null ? fmtPct(data.expected_volatility) : "—"} sub="annualized" />
                  <${StatCard} label="Sharpe Ratio" value=${data.sharpe_ratio !== null ? data.sharpe_ratio.toFixed(2) : "—"} />
                  <${StatCard} label="Trading Days Used" value=${data.days_of_history} />
                </div>
                <div class="panel panel-pad">
                  <div class="panel-title">Methodology</div>
                  <p style=${{ color: "var(--text-dim)", fontSize: "12px", lineHeight: 1.6, margin: 0 }}>
                    Standard mean-variance optimization (Markowitz 1952) over each strategy's own realized daily
                    P&L — deliberately the boring, textbook version, no shrinkage or leverage. The estimate only gets
                    more meaningful as more real trading days accumulate; it is not a promise of future performance.
                  </p>
                </div>
              </div>
            </div>
          `}
    </div>
  `;
}
