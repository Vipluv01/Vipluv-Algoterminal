import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { fmtMoney, pnlClass } from "../format.js";

export function Accounts() {
  const [account, setAccount] = React.useState(null);
  React.useEffect(() => { api.account().then(setAccount); }, []);

  return html`
    <div class="page fade-in">
      <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Accounts</h1>
      <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginBottom: "20px" }}>Trading mode and broker connection</div>

      <div class="accounts-grid">
        <div class="panel panel-pad">
          <div class="panel-title">Trading Mode</div>
          <div style=${{ display: "flex", gap: "10px" }}>
            <div class="chip active" style=${{ flex: 1, minWidth: 0, whiteSpace: "normal", textAlign: "center", cursor: "default" }}>
              <span class="badge badge-live" style=${{ marginRight: "8px" }}>●</span>Paper
            </div>
            <div class="chip" style=${{ flex: 1, minWidth: 0, whiteSpace: "normal", textAlign: "center", cursor: "not-allowed", opacity: 0.5 }} title="Requires a connected broker">
              Live (locked)
            </div>
          </div>
          <p style=${{ color: "var(--text-dim)", fontSize: "12px", lineHeight: 1.6, marginTop: "14px", marginBottom: 0 }}>
            Every strategy and manual order runs in paper mode against the real bourse matching engine
            with simulated capital. Live mode unlocks once a broker is connected below, and even then
            every live order requires explicit human confirmation before it reaches the broker — no
            code path here can place a real trade automatically.
          </p>
        </div>

        <div class="panel panel-pad">
          <div class="panel-title">Broker Connection</div>
          <div style=${{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
            <span class="status-dot dead" />
            <span style=${{ fontWeight: 700 }}>Angel One SmartAPI</span>
            <span class="badge badge-off" style=${{ marginLeft: "auto" }}>Not Connected</span>
          </div>
          <p style=${{ color: "var(--text-dim)", fontSize: "12px", lineHeight: 1.6, marginTop: 0 }}>
            Connecting requires an Angel One developer account and API credentials, entered once and
            encrypted at rest — never stored or logged in plaintext. Not yet wired up in this build.
          </p>
        </div>

        <div class="panel panel-pad" style=${{ gridColumn: "1 / -1" }}>
          <div class="panel-title">Paper Account Summary</div>
          ${!account
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
