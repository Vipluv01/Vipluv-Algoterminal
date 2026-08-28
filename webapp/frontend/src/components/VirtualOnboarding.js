import React from "react";
import { html } from "../html.js";
import { Modal } from "./Modal.js";
import { inr } from "../format.js";

const ONBOARDED_KEY = "algoterminal:virtualOnboarded";
// ₹1 crore -- chosen by the team over the original spec's $100k because
// this whole platform is NSE/rupee-denominated (see the Phase 7 prompt);
// a fixed literal here, not derived from any backend constant, since this
// screen's only job is to set correct EXPECTATIONS before the real
// virtual-capital ledger (Phase 7, bourse 2) exists to enforce it.
export const VIRTUAL_STARTING_CAPITAL = 1_00_00_000;

export function hasSeenVirtualOnboarding() {
  try {
    return window.localStorage.getItem(ONBOARDED_KEY) === "true";
  } catch {
    return false; // storage unavailable -- show onboarding every time rather than assume seen
  }
}

function markVirtualOnboarded() {
  try {
    window.localStorage.setItem(ONBOARDED_KEY, "true");
  } catch {
    /* non-fatal -- worst case the onboarding shows again next time */
  }
}

// Shown once, the first time a user ever selects Virtual mode -- distinct
// in framing from BOTH paper (₹1,00,000 reference capital, the low-stakes
// practice default) and live (a real broker, real money): virtual is
// still entirely simulated, just at a scale meant for testing a strategy
// the way it would actually be sized before ever risking real capital.
export function VirtualOnboardingModal({ onConfirm, onClose }) {
  function confirm() {
    markVirtualOnboarded();
    onConfirm();
  }

  return html`
    <${Modal} title="Switch to Virtual Mode" onClose=${onClose} size="sm">
      <p style=${{ color: "var(--text-dim)", fontSize: "12.5px", lineHeight: 1.6, marginTop: 0 }}>
        Virtual mode starts you with <strong style=${{ color: "var(--text)" }}>${inr(VIRTUAL_STARTING_CAPITAL, { decimals: 0 })}</strong> in
        simulated capital — entirely separate from Paper's ${inr(100_000, { decimals: 0 })} reference balance.
      </p>
      <ul style=${{ color: "var(--text-dim)", fontSize: "12px", lineHeight: 1.7, paddingLeft: "18px", margin: "0 0 14px" }}>
        <li>No real money at risk — this is simulated capital, not connected to a broker.</li>
        <li>Meant for testing a strategy at realistic position sizes before ever going live.</li>
        <li>Separate from Paper's own balance and trade history.</li>
      </ul>
      <button class="btn btn-block btn-primary" onClick=${confirm}>Start Virtual Trading</button>
    <//>
  `;
}
