import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, pnlClass } from "../format.js";
import { useMode, setMode, setLiveMode, MODE_BLOCKED_REASON, useLiveReadiness } from "../mode.js";
import { LiveConfirmModal } from "../components/ModeSwitcher.js";
import { VirtualOnboardingModal, hasSeenVirtualOnboarding } from "../components/VirtualOnboarding.js";
import { useToast } from "../toast.js";

const MODE_ORDER = ["paper", "virtual", "live"];

// paper -> GET /account, virtual -> GET /virtual/account (Mode.virtual's
// own Rs 1cr-starting book, see app/routers/virtual.py). Live has no
// account-snapshot endpoint yet -- shown as an honest "not available yet"
// note below rather than silently leaving the PREVIOUS mode's numbers on
// screen mislabeled as live's.
function useAccountForMode(mode) {
  const [account, setAccount] = React.useState(null);
  React.useEffect(() => {
    setAccount(null);
    if (mode === "paper") api.account().then(setAccount).catch(() => {});
    else if (mode === "virtual") api.virtual.account().then(setAccount).catch(() => {});
  }, [mode]);
  return account;
}

function BrokerConnectionPanel() {
  const [cred, setCred] = React.useState(undefined); // undefined = loading, null = none stored
  React.useEffect(() => { api.vault.get().then(setCred).catch(() => setCred(null)); }, []);

  const connected = !!cred;
  const complete = connected && cred.client_code_last4 && cred.has_totp_secret;

  return html`
    <div class="panel panel-pad">
      <div class="panel-title">Broker Connection</div>
      ${cred === undefined
        ? html`<div class="skeleton" style=${{ height: "40px" }} />`
        : html`
          <div style=${{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
            <span class=${`status-dot ${connected ? "live" : "dead"}`} />
            <span style=${{ fontWeight: 700 }}>${connected ? cred.broker : "Angel One SmartAPI"}</span>
            <span class=${`badge ${connected ? (complete ? "badge-live" : "badge-off") : "badge-off"}`} style=${{ marginLeft: "auto" }}>
              ${connected ? (complete ? "Connected" : "Incomplete") : "Not Connected"}
            </span>
          </div>
        `}
      <p style=${{ color: "var(--text-dim)", fontSize: "12px", lineHeight: 1.6, marginTop: 0, marginBottom: "10px" }}>
        ${connected
          ? (complete
            ? `Credential on file, last rotated ${new Date(cred.rotated_at).toLocaleDateString()}.`
            : "Credential is missing a client code and/or TOTP secret — live mode will stay blocked until both are added.")
          : "Connecting requires an Angel One developer account and API credentials, entered once and encrypted at rest — never stored or logged in plaintext."}
      </p>
      <a href="#/settings" class="btn btn-sm btn-ghost">${connected ? "Manage in Vault" : "Add Credential"} →</a>
    </div>
  `;
}

export function Accounts() {
  const mode = useMode();
  const account = useAccountForMode(mode);
  const [confirmTarget, setConfirmTarget] = React.useState(null);
  const [virtualOnboardingOpen, setVirtualOnboardingOpen] = React.useState(false);
  const toast = useToast();
  // Checked once per page visit (this page has no open/closed picker
  // state the way ModeSwitcher's dropdown does) -- see mode.js.
  const liveReadiness = useLiveReadiness(true);

  function blockedReasonFor(m) {
    if (m === "live") return liveReadiness.status === "ready" ? null : (liveReadiness.reason || "Checking broker connection…");
    return MODE_BLOCKED_REASON[m];
  }

  function selectMode(next) {
    if (next === mode || blockedReasonFor(next)) return;
    if (next === "live") { setConfirmTarget(next); return; }
    if (next === "virtual" && !hasSeenVirtualOnboarding()) { setVirtualOnboardingOpen(true); return; }
    setMode(next);
  }

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Accounts</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>Trading mode and broker connection</div>

      <div class="accounts-grid">
        <div class="panel panel-pad">
          <div class="panel-title">Trading Mode</div>
          <div style=${{ display: "flex", gap: "10px" }}>
            ${MODE_ORDER.map((m) => {
              const blocked = blockedReasonFor(m);
              const active = m === mode;
              return html`
                <div key=${m}
                     class=${`chip ${active ? "active" : ""}`}
                     style=${{ flex: 1, minWidth: 0, whiteSpace: "normal", textAlign: "center", textTransform: "capitalize", cursor: blocked ? "not-allowed" : "pointer", opacity: blocked ? 0.5 : 1 }}
                     title=${blocked || undefined}
                     onClick=${() => selectMode(m)}>
                  ${active && html`<span class=${`badge badge-mode-${m}`} style=${{ marginRight: "8px" }}>●</span>`}
                  ${m}${blocked ? " (locked)" : ""}
                </div>
              `;
            })}
          </div>
          <p style=${{ color: "var(--text-dim)", fontSize: "12px", lineHeight: 1.6, marginTop: "14px", marginBottom: 0 }}>
            Paper and Virtual both run against the real bourse matching engine with simulated capital
            (Paper: ₹1,00,000, Virtual: ₹1,00,00,000). Live mode unlocks once a complete broker credential
            is on file below, and even then every live order requires explicit human confirmation before
            it reaches the broker — no code path here can place a real trade automatically.
          </p>
          ${confirmTarget && html`
            <${LiveConfirmModal}
              onConfirm=${async () => {
                const { ok, reason } = await setLiveMode();
                if (!ok) toast(reason || "Could not switch to live — broker connection is no longer ready.", "err");
                setConfirmTarget(null);
              }}
              onClose=${() => setConfirmTarget(null)} />
          `}
          ${virtualOnboardingOpen && html`
            <${VirtualOnboardingModal}
              onConfirm=${() => { setMode("virtual"); setVirtualOnboardingOpen(false); }}
              onClose=${() => setVirtualOnboardingOpen(false)} />
          `}
        </div>

        <${BrokerConnectionPanel} />

        <div class="panel panel-pad" style=${{ gridColumn: "1 / -1" }}>
          <div class="panel-title">${mode === "virtual" ? "Virtual" : mode === "live" ? "Live" : "Paper"} Account Summary</div>
          ${mode === "live"
            ? html`<div style=${{ color: "var(--text-faint)", fontSize: "12px", padding: "10px 0" }}>Live account snapshots aren't available yet — check back once this phase's broker integration is complete.</div>`
            : !account
              ? html`<div class="skeleton" style=${{ height: "60px" }} />`
              : html`
                <div style=${{ display: "flex", flexWrap: "wrap", gap: "24px 32px" }}>
                  <div>
                    <div class="stat-label">Cash</div>
                    <div class="mono" style=${{ fontWeight: 600, fontSize: "16px" }}>${fmtMoney(account.cash)}</div>
                  </div>
                  <div>
                    <div class="stat-label">Total Value</div>
                    <div class="mono" style=${{ fontWeight: 600, fontSize: "16px" }}>${fmtMoney(account.total_value)}</div>
                  </div>
                  <div>
                    <div class="stat-label">Open Positions</div>
                    <div class="mono" style=${{ fontWeight: 600, fontSize: "16px" }}>${account.positions.length}</div>
                  </div>
                  <div>
                    <div class="stat-label">Unrealized P&L</div>
                    <div class=${`mono ${pnlClass(account.total_unrealized_pnl)}`} style=${{ fontWeight: 600, fontSize: "16px" }}>${fmtMoney(account.total_unrealized_pnl)}</div>
                  </div>
                </div>
              `}
        </div>
      </div>
    </div>
  `;
}
