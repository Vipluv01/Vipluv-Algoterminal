import React from "react";
import { html } from "../html.js";
import { useMode, setMode, MODE_BLOCKED_REASON } from "../mode.js";
import { Modal } from "./Modal.js";

const MODE_ORDER = ["paper", "virtual", "live"];
const CONFIRM_WORD = "LIVE";

// Live never gets a one-click toggle, even once it's unblocked -- typing
// the mode's name is a small, deliberate speed bump between "I clicked
// near the right area" and "this account can place a real trade."
export function LiveConfirmModal({ onConfirm, onClose }) {
  const [typed, setTyped] = React.useState("");
  const canConfirm = typed.trim().toUpperCase() === CONFIRM_WORD;

  return html`
    <${Modal} title="Switch to Live Mode" onClose=${onClose} size="sm">
      <p style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6, marginTop: 0 }}>
        Live mode places real orders through a connected broker with real capital.
        Type <strong>${CONFIRM_WORD}</strong> to confirm.
      </p>
      <div class="field">
        <input class="input" value=${typed} onInput=${(e) => setTyped(e.target.value)}
               placeholder=${CONFIRM_WORD} autofocus />
      </div>
      <button class="btn btn-block btn-sell" disabled=${!canConfirm} onClick=${onConfirm}>
        Confirm Switch to Live
      </button>
    <//>
  `;
}

export function ModeSwitcher() {
  const mode = useMode();
  const [open, setOpen] = React.useState(false);
  const [confirmTarget, setConfirmTarget] = React.useState(null);
  const rootRef = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function selectMode(next) {
    if (next === mode) { setOpen(false); return; }
    if (MODE_BLOCKED_REASON[next]) return; // disabled options aren't clickable, but guard anyway
    if (next === "live") {
      setConfirmTarget(next);
      setOpen(false);
      return;
    }
    setMode(next);
    setOpen(false);
  }

  return html`
    <div class="mode-switcher" ref=${rootRef}>
      <button class=${`badge badge-mode-${mode}`} style=${{ cursor: "pointer", border: "none" }}
              onClick=${() => setOpen((v) => !v)} aria-haspopup="true" aria-expanded=${open}>
        ${mode.toUpperCase()} MODE
      </button>
      ${open && html`
        <div class="mode-dropdown">
          ${MODE_ORDER.map((m) => {
            const blocked = MODE_BLOCKED_REASON[m];
            return html`
              <div key=${m} class=${`mode-option ${blocked ? "mode-option-blocked" : ""}`}
                   title=${blocked || undefined}
                   role="button" tabindex=${blocked ? "-1" : "0"}
                   onClick=${() => !blocked && selectMode(m)}
                   onKeyDown=${(e) => { if (!blocked && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); selectMode(m); } }}>
                <span class="mode-option-label">${m}${m === mode ? " (current)" : ""}</span>
                ${blocked && html`<span class="mode-option-reason">${blocked}</span>`}
              </div>
            `;
          })}
        </div>
      `}
      ${confirmTarget && html`
        <${LiveConfirmModal}
          onConfirm=${() => { setMode(confirmTarget); setConfirmTarget(null); }}
          onClose=${() => setConfirmTarget(null)} />
      `}
    </div>
  `;
}
