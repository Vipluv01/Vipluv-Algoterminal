import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useToast } from "../toast.js";
import { refreshRiskStatus, useTradingHalted } from "../riskStatus.js";

// {key, label, body, decimals?, isPercent?} -- every field
// RiskControlsUpdate (app/routers/risk.py) actually accepts, editable here.
// The previous version of this page displayed "Pairs Cointegration
// p-value Ceiling" by reading risk.pairs_coint_pvalue_max -- a field that
// doesn't exist on RiskControlsOut (the real name is coint_pvalue_max, no
// pairs_ prefix), so that card always silently rendered undefined. Fixed
// below as part of making every one of these fields real and editable,
// not just re-displaying the same typo with an input box added.
const FIELDS = [
  { key: "max_order_qty", label: "Max Order Quantity", body: "Hard ceiling on every single order, paper or live — independent of any strategy's own sizing logic." },
  { key: "kelly_multiplier", label: "Fractional Kelly Multiplier", body: "Applied on top of the full Kelly fraction — full Kelly is growth-optimal but high-variance under estimation error, so this scales it down." },
  { key: "max_position_fraction", label: "Max Position Fraction", body: "Hard ceiling on account fraction per position, independent of what the Kelly math computes — no single calculation is trusted to bound itself." },
  { key: "daily_max_drawdown_pct", label: "Daily Max Drawdown", body: "Trips the circuit breaker for the rest of the trading day once realized loss crosses this fraction of account value." },
  { key: "pairs_entry_z", label: "Pairs Entry Z-Score", body: "Spread must deviate at least this many standard deviations from its rolling mean before the pairs strategy enters." },
  { key: "pairs_exit_z", label: "Pairs Exit Z-Score", body: "Normal, non-stop exit threshold — the spread reverting this close to its mean closes the position on schedule." },
  { key: "pairs_stop_z", label: "Pairs Stop Z-Score", body: "Force-closes the position regardless of the normal exit threshold — takes priority over everything else." },
  { key: "coint_pvalue_max", label: "Cointegration p-value Ceiling", body: "Engle-Granger test must clear this before any pairs signal fires at all — the check the old Algo Terminal skipped." },
];

function ControlCard({ field, value, editValue, onChange, dirty }) {
  return html`
    <div class="stat-card">
      <div class="stat-label">${field.label}</div>
      <div class="field" style=${{ marginTop: "6px", marginBottom: "6px" }}>
        <input class="input" type="number" step="any" value=${editValue}
               onInput=${(e) => onChange(e.target.value)} style=${{ fontSize: "16px", fontWeight: 700 }} />
      </div>
      <div class="stat-sub">
        ${field.body}
        ${dirty && html`<span style=${{ color: "var(--accent-bright)", display: "block", marginTop: "4px" }}>Unsaved</span>`}
      </div>
    </div>
  `;
}

export function Risk() {
  const [risk, setRisk] = React.useState(null);
  const [edits, setEdits] = React.useState({}); // key -> string (raw input value)
  const [saving, setSaving] = React.useState(false);
  // Real bug fixed 2026-09-04: "Clear Halt" used to fire immediately on
  // click, no confirmation at all -- a single misclick silently dropped
  // every restriction the circuit breaker had just imposed (the daily
  // drawdown limit that TRIPPED the halt in the first place). This is a
  // two-step affordance instead of a browser confirm() dialog, matching
  // this app's own styled-UI convention rather than a native popup that
  // would look out of place here.
  const [confirmingClearHalt, setConfirmingClearHalt] = React.useState(false);
  const [clearingHalt, setClearingHalt] = React.useState(false);
  const toast = useToast();
  // Real bug found live via a Playwright walkthrough, 2026-09-04: this
  // page's own halted badge/button read risk.trading_halted, a value
  // fetched ONCE on mount and only ever updated by THIS page's own
  // resetHalt() call -- clearing the halt from App.js's global banner
  // instead (a different component instance) left this page stuck
  // showing "HALTED" with a "Clear Halt" button that no longer did
  // anything meaningful, even though the halt was genuinely cleared
  // (confirmed: the global banner disappeared, the toast fired, GET
  // /risk really did return trading_halted=false). riskStatus.js's own
  // useTradingHalted() is the SAME shared, polled store the banner reads
  // from -- driving the badge/button off that instead of the one-shot
  // `risk` fetch keeps this page in sync with a halt cleared (or
  // tripped) from anywhere, not just from here.
  const halted = useTradingHalted();

  const load = React.useCallback(() => {
    api.risk.get().then((r) => { setRisk(r); setEdits({}); });
  }, []);
  React.useEffect(() => { load(); }, [load]);
  // If the halt was cleared elsewhere while this page had the
  // confirmation step open, that confirmation is now stale -- drop back
  // to the plain (now hidden, since !halted) state rather than leaving
  // "Confirm Clear" sitting there for an action that already happened.
  React.useEffect(() => { if (!halted) setConfirmingClearHalt(false); }, [halted]);

  function editValueFor(field) {
    if (field.key in edits) return edits[field.key];
    if (!risk) return "";
    return String(risk[field.key]);
  }

  const dirtyKeys = Object.keys(edits).filter((k) => risk && String(risk[k]) !== edits[k]);
  const hasDirty = dirtyKeys.length > 0;

  async function save() {
    const body = {};
    for (const k of dirtyKeys) {
      const num = Number(edits[k]);
      if (Number.isNaN(num)) return toast(`${k} isn't a valid number`, "err");
      body[k] = num;
    }
    setSaving(true);
    try {
      const updated = await api.risk.update(body);
      setRisk(updated);
      setEdits({});
      await refreshRiskStatus(); // in case a change interacts with the halt state's own display elsewhere
      toast("Risk controls updated", "ok");
    } catch (e) {
      toast(e.message || "Could not save risk controls", "err");
    } finally {
      setSaving(false);
    }
  }

  async function resetHalt() {
    setClearingHalt(true);
    try {
      const updated = await api.risk.resetHalt();
      setRisk(updated);
      setConfirmingClearHalt(false);
      await refreshRiskStatus();
      toast("Trading halt cleared — every restriction it imposed is lifted", "ok");
    } catch (e) {
      toast(e.message || "Could not clear the halt", "err");
    } finally {
      setClearingHalt(false);
    }
  }

  return html`
    <div class="page fade-in">
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "10px", marginBottom: "20px" }}>
        <div>
          <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Risk</h1>
          <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>
            What the system actually enforces on every order, paper or live — edit and save to change it for real.
          </div>
        </div>
        ${risk && html`
          <div style=${{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span class=${`badge ${halted ? "badge-off" : "badge-live"}`}>
              ${halted ? "● CIRCUIT BREAKER: HALTED" : "● CIRCUIT BREAKER: ARMED"}
            </span>
            ${halted && !confirmingClearHalt && html`
              <button class="btn btn-sm" onClick=${() => setConfirmingClearHalt(true)}>Clear Halt</button>
            `}
            ${halted && confirmingClearHalt && html`
              <${React.Fragment}>
                <span style=${{ fontSize: "11.5px", color: "var(--text-dim)" }}>Clear every restriction this halt imposed?</span>
                <button class="btn btn-sm btn-sell" disabled=${clearingHalt} onClick=${resetHalt}>
                  ${clearingHalt ? "Clearing…" : "Confirm Clear"}
                </button>
                <button class="btn btn-sm btn-ghost" disabled=${clearingHalt} onClick=${() => setConfirmingClearHalt(false)}>Cancel</button>
              <//>
            `}
          </div>
        `}
      </div>

      ${!risk
        ? html`<div class="skeleton" style=${{ height: "160px" }} />`
        : html`
          <${React.Fragment}>
            <div style=${{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px" }} class="dash-stats">
              ${FIELDS.map((f) => html`
                <${ControlCard} key=${f.key} field=${f} editValue=${editValueFor(f)}
                                dirty=${dirtyKeys.includes(f.key)}
                                onChange=${(v) => setEdits((e) => ({ ...e, [f.key]: v }))} />
              `)}
            </div>
            <div style=${{ display: "flex", gap: "10px", marginTop: "16px" }}>
              <button class="btn btn-primary" disabled=${!hasDirty || saving} onClick=${save}>
                ${saving ? "Saving…" : `Save Changes${hasDirty ? ` (${dirtyKeys.length})` : ""}`}
              </button>
              ${hasDirty && html`<button class="btn btn-ghost" onClick=${() => setEdits({})} disabled=${saving}>Discard</button>`}
            </div>
          <//>
        `}
    </div>
  `;
}
