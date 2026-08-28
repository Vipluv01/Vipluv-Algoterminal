import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useToast } from "../toast.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { EmptyState } from "../components/EmptyState.js";

const BROKERS = [
  { value: "angelone", label: "Angel One" },
  { value: "zerodha", label: "Zerodha" },
  { value: "groww", label: "Groww" },
];

// Angel One's SmartAPI login has no separate "api secret" the way Zerodha
// does -- api_secret is repurposed to hold the account's trading password/
// MPIN for this broker specifically (see LiveBrokerCredential's own
// docstring in app/models/trading.py). client_code and totp_secret are
// both genuinely REQUIRED for a real Angel One login even though the
// backend schema only enforces api_key/api_secret -- app/broker/
// angelone.py (not yet built) is what actually enforces the rest, so
// letting an incomplete Angel One credential save here would look
// configured and then silently fail the first time it's used, exactly
// the failure mode this phase is designed to avoid (see mode.js).
const IS_ANGEL_ONE = (broker) => broker === "angelone";

// STORE-ONLY screen. api_key/api_secret/access_token/client_code/
// totp_secret all live in this component's own state ONLY between the
// moment they're typed and the moment the POST resolves -- cleared
// immediately after (success or failure) rather than kept around, never
// written to localStorage, never logged, and never echoed into a toast or
// error message (only a fixed label like "Angel One" or the server's own
// generic error detail ever appears there). The server itself never sends
// a decrypted secret back (see app/routers/vault.py) -- there is no code
// path in this file that could render one even by accident, because a
// full plaintext value is never present in anything read FROM the
// server, only in what's typed and about to be sent TO it.
function CredentialForm({ existing, onClose, onSaved }) {
  const [broker, setBroker] = React.useState(existing?.broker || BROKERS[0].value);
  const [apiKey, setApiKey] = React.useState("");
  const [apiSecret, setApiSecret] = React.useState("");
  const [accessToken, setAccessToken] = React.useState("");
  const [clientCode, setClientCode] = React.useState("");
  const [totpSecret, setTotpSecret] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const toast = useToast();
  const isAngelOne = IS_ANGEL_ONE(broker);

  function clearSensitiveFields() {
    setApiKey("");
    setApiSecret("");
    setAccessToken("");
    setClientCode("");
    setTotpSecret("");
  }

  async function submit() {
    if (!apiKey || !apiSecret) return toast(`API key and ${isAngelOne ? "trading password/MPIN" : "secret"} are both required`, "err");
    if (isAngelOne && (!clientCode || !totpSecret)) return toast("Client code and TOTP secret are both required for Angel One", "err");
    setSaving(true);
    try {
      const saved = await api.vault.put({
        broker,
        api_key: apiKey,
        api_secret: apiSecret,
        access_token: accessToken || null,
        client_code: clientCode || null,
        totp_secret: totpSecret || null,
      });
      onSaved(saved);
      toast(existing ? "Credential rotated" : "Credential stored", "ok");
    } catch (e) {
      toast(e.message || "Could not store the credential", "err");
    } finally {
      // Cleared unconditionally -- a failed submit is exactly the case
      // where a stale plaintext value must NOT linger in memory waiting
      // for a retry the user may not even make.
      clearSensitiveFields();
      setSaving(false);
    }
  }

  return html`
    <div class="panel panel-pad" style=${{ maxWidth: "480px" }}>
      <div class="panel-title">${existing ? "Rotate Credential" : "Add Broker Credential"}</div>
      <div style=${{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "10px" }}>
        <div class="field">
          <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>Broker</label>
          <select class="input" value=${broker} onChange=${(e) => setBroker(e.target.value)}>
            ${BROKERS.map((b) => html`<option key=${b.value} value=${b.value}>${b.label}</option>`)}
          </select>
        </div>
        <div class="field">
          <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>API Key</label>
          <input class="input" type="password" autocomplete="off" value=${apiKey} onInput=${(e) => setApiKey(e.target.value)} />
        </div>
        <div class="field">
          <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>${isAngelOne ? "Trading Password / MPIN" : "API Secret"}</label>
          <input class="input" type="password" autocomplete="off" value=${apiSecret} onInput=${(e) => setApiSecret(e.target.value)} />
        </div>
        ${isAngelOne && html`
          <div class="field">
            <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>Client Code</label>
            <input class="input" type="password" autocomplete="off" value=${clientCode} onInput=${(e) => setClientCode(e.target.value)} />
          </div>
          <div class="field">
            <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>TOTP Secret</label>
            <input class="input" type="password" autocomplete="off" value=${totpSecret} onInput=${(e) => setTotpSecret(e.target.value)} />
            <div style=${{ color: "var(--text-faint)", fontSize: "11px", marginTop: "4px" }}>
              The base32 seed from enabling TOTP in the Angel One app — not a live 6-digit code, which expires in seconds.
            </div>
          </div>
        `}
        <div class="field">
          <label style=${{ fontSize: "12px", color: "var(--text-faint)" }}>Access Token <span style=${{ color: "var(--text-faint)" }}>(optional)</span></label>
          <input class="input" type="password" autocomplete="off" value=${accessToken} onInput=${(e) => setAccessToken(e.target.value)} />
        </div>
        <div style=${{ color: "var(--text-faint)", fontSize: "11.5px" }}>
          Stored encrypted at rest. Once saved, this screen can only ever show the last 4 characters back — there's no way to retrieve the full value again, from here or anywhere else.
        </div>
        <div style=${{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
          <button class="btn btn-ghost" onClick=${onClose} disabled=${saving}>Cancel</button>
          <button class="btn btn-primary" onClick=${submit} disabled=${saving}>${saving ? "Storing…" : "Store Credential"}</button>
        </div>
      </div>
    </div>
  `;
}

function CredentialCard({ cred, onRotate, onRemove }) {
  const [removing, setRemoving] = React.useState(false);
  const [confirming, setConfirming] = React.useState(false);

  async function remove() {
    setRemoving(true);
    try {
      await onRemove();
    } finally {
      setRemoving(false);
      setConfirming(false);
    }
  }

  const isAngelOne = IS_ANGEL_ONE(cred.broker);
  const brokerLabel = BROKERS.find((b) => b.value === cred.broker)?.label || cred.broker;

  return html`
    <div class="panel panel-pad" style=${{ maxWidth: "480px" }}>
      <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div class="panel-title" style=${{ marginBottom: "10px" }}>${brokerLabel}</div>
          <div class="stat-card" style=${{ marginBottom: "8px" }}>
            <div class="stat-label">API Key</div>
            <div class="stat-value mono">•••• ${cred.api_key_last4}</div>
          </div>
          <div class="stat-card" style=${{ marginBottom: "8px" }}>
            <div class="stat-label">${isAngelOne ? "Trading Password / MPIN" : "API Secret"}</div>
            <div class="stat-value mono">•••• ${cred.api_secret_last4}</div>
          </div>
          ${cred.client_code_last4 && html`
            <div class="stat-card" style=${{ marginBottom: "8px" }}>
              <div class="stat-label">Client Code</div>
              <div class="stat-value mono">•••• ${cred.client_code_last4}</div>
            </div>
          `}
          <div style=${{ fontSize: "12px", color: "var(--text-faint)" }}>
            ${cred.has_access_token ? "Access token on file. " : "No access token on file. "}
            ${isAngelOne ? (cred.has_totp_secret ? "TOTP secret on file. " : "⚠ No TOTP secret on file — Angel One login will fail without one. ") : ""}
            Rotated ${new Date(cred.rotated_at).toLocaleString()}
          </div>
        </div>
      </div>
      <div style=${{ display: "flex", gap: "10px", marginTop: "16px" }}>
        <button class="btn btn-sm" onClick=${onRotate}>Rotate</button>
        ${!confirming
          ? html`<button class="btn btn-sm btn-ghost" onClick=${() => setConfirming(true)}>Remove</button>`
          : html`
            <${React.Fragment}>
              <button class="btn btn-sm" style=${{ background: "var(--pnl-neg-dim)", color: "var(--pnl-neg-bright)" }} onClick=${remove} disabled=${removing}>
                ${removing ? "Removing…" : "Confirm Remove"}
              </button>
              <button class="btn btn-sm btn-ghost" onClick=${() => setConfirming(false)} disabled=${removing}>Cancel</button>
            <//>
          `}
      </div>
    </div>
  `;
}

export function Vault() {
  const [cred, setCred] = React.useState(undefined); // undefined = loading, null = none stored
  const [error, setError] = React.useState(null);
  const [formOpen, setFormOpen] = React.useState(false);
  const toast = useToast();

  const load = React.useCallback(() => {
    setError(null);
    api.vault.get().then(setCred).catch((e) => setError(e.message || "Could not load vault status"));
  }, []);
  React.useEffect(() => { load(); }, [load]);

  async function remove() {
    try {
      await api.vault.delete();
      setCred(null);
      toast("Credential removed", "ok");
    } catch (e) {
      toast(e.message || "Could not remove the credential", "err");
    }
  }

  function onSaved(saved) {
    setCred(saved);
    setFormOpen(false);
  }

  return html`
    <div class="page fade-in">
      <div style=${{ marginBottom: "20px" }}>
        <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Vault</h1>
        <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>Live broker credentials. Store-only — a secret entered here can never be viewed again, only rotated or removed.</div>
      </div>

      <${ErrorBoundary} label="Vault">
        ${cred === undefined && !error && html`<div class="skeleton" style=${{ height: "220px", maxWidth: "480px" }} />`}
        ${error && html`
          <div class="error-state" style=${{ maxWidth: "480px" }}>
            <div>
              <div class="error-state-title">Could not load vault status</div>
              <div class="error-state-detail">${error}</div>
            </div>
            <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
          </div>
        `}
        ${cred === null && !error && !formOpen && html`
          <${EmptyState} message="No broker credential stored yet." actionLabel="Add Credential" onAction=${() => setFormOpen(true)} />
        `}
        ${cred && !formOpen && html`<${CredentialCard} cred=${cred} onRotate=${() => setFormOpen(true)} onRemove=${remove} />`}
        ${formOpen && html`<${CredentialForm} existing=${cred} onClose=${() => setFormOpen(false)} onSaved=${onSaved} />`}
      <//>
    </div>
  `;
}
