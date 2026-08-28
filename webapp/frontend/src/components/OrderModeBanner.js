import React from "react";
import { html } from "../html.js";
import { useMode } from "../mode.js";

// Every surface that can actually submit an order needs this, not just
// ModeSwitcher's own badge -- a user whose eyes are on the ticket, not the
// nav, should never be able to mistake live for paper/virtual. Paper (the
// calm default) gets nothing here on purpose: a banner on every single
// ticket, always, would train a viewer to stop reading it, which defeats
// the point for the one mode where it actually matters.
const COPY = {
  paper: null,
  virtual: "Simulated capital — no real money at risk.",
  live: "LIVE — this places a REAL order with REAL money.",
};

export function OrderModeBanner() {
  const mode = useMode();
  const copy = COPY[mode];
  if (!copy) return null;
  return html`
    <div class=${`order-mode-banner order-mode-banner-${mode}`}>
      ${mode === "live" ? "⚠ " : ""}${copy}
    </div>
  `;
}
