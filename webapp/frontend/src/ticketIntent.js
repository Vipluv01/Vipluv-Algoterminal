// A single, one-shot piece of state carried across a hash navigation: "the
// next time the Trade page mounts, prefill its side to this." Exists so
// the B/S keyboard shortcuts (keyboard.js) can PRE-FILL an order ticket
// without the Trade page needing to know keyboard.js exists, and without
// smuggling the value through the URL.
//
// SAFETY: this module has no way to submit an order and never will --
// it only carries a side ("buy"/"sell") for a form field's INITIAL value.
// The ticket itself still requires an explicit click/Enter on its own
// Submit button, same as always. See keyboard.js's B/S registration and
// ManualTrade.js's consumeTicketIntent() call for the two ends of this.
let intent = null;

export function setTicketIntent(next) {
  intent = next;
}

// One-shot: reading it clears it, so navigating to Trade again later
// (without a fresh shortcut press) doesn't re-apply a stale prefill.
export function consumeTicketIntent() {
  const current = intent;
  intent = null;
  return current;
}
