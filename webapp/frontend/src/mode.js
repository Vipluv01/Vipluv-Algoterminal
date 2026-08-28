// Trading mode (paper/virtual/live) as real app state -- previously two
// separate hardcoded string literals ("PAPER MODE" in App.js's nav badge,
// "MODE: PAPER" in StatusBar.js) that could never agree or disagree with
// each other because neither one was actually state; both now read from
// here. Same external-store shape as theme.js's theme/density (one
// source of truth, persisted, subscribable), for the same reason.
import React from "react";
import { api } from "./api.js";

const MODE_KEY = "algoterminal:mode";
const VALID_MODES = ["paper", "virtual", "live"];
const DEFAULT_MODE = "paper";

// paper and virtual are both unconditionally available now: virtual has
// no per-user setup step (routers/virtual.py computes its account view
// from Order.mode==virtual the same way paper's does -- an account with
// zero orders is just a fresh Rs 1cr balance, not an error). Live is
// deliberately NOT in this static object -- whether it's usable depends
// on real per-user state (a stored, COMPLETE broker credential) that can
// change at any time, so it needs an actual API check, not a flag. See
// useLiveReadiness below -- every place that needs to know "can live
// actually be selected right now" (ModeSwitcher, Accounts' chips) calls
// the same hook and gets the same answer instead of drifting.
export const MODE_BLOCKED_REASON = {
  paper: null,
  virtual: null,
};

// Mirrors app/broker/adapter_cache.py's get_adapter_for_user EXACTLY --
// same two conditions (a credential row exists; it has both client_code
// and totp_secret) -- so this can never claim "ready" in a case the
// backend would actually reject the first time a live order is
// confirmed. It cannot verify the credential is genuinely valid against
// Angel One itself (no way to know that without a real login attempt),
// only that it's COMPLETE enough for the backend to attempt one.
async function checkLiveReadinessNow() {
  try {
    const cred = await api.vault.get();
    if (!cred) return { status: "blocked", reason: "No broker credential stored — add one in Vault first." };
    if (!cred.client_code_last4 || !cred.has_totp_secret) {
      return { status: "blocked", reason: "Broker credential is missing client code and/or TOTP secret — both are required for Angel One login." };
    }
    return { status: "ready", reason: null };
  } catch {
    return { status: "blocked", reason: "Could not check broker credential status." };
  }
}

export function useLiveReadiness(active) {
  const [state, setState] = React.useState({ status: "checking", reason: null });

  React.useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setState({ status: "checking", reason: null });
    checkLiveReadinessNow().then((result) => { if (!cancelled) setState(result); });
    return () => { cancelled = true; };
    // Re-runs on every false->true transition of `active` (e.g. the mode
    // picker being opened again) -- deliberately not cached across opens,
    // since a credential added or rotated in Vault since the last check
    // must be reflected immediately, not on some stale cadence. This is
    // still only fresh as of when the picker opened, not as of the exact
    // moment a caller later flips the mode -- see setLiveMode below for
    // the version that re-checks immediately before acting.
  }, [active]);

  return state;
}

// The version LiveConfirmModal's own confirm button should call, NOT
// setMode(next, { liveReadiness }) directly with a hook value that may
// have been sitting around since the picker was opened -- a credential
// rotated or removed in the seconds between opening the confirm dialog
// and clicking confirm (rare, but the typed-word friction makes the gap
// real, not hypothetical) must not ride through on a stale "ready".
// Re-checks right here, then calls the real enforcement point with a
// same-instant result.
export async function setLiveMode() {
  const fresh = await checkLiveReadinessNow();
  const ok = setMode("live", { liveReadiness: fresh });
  return { ok, reason: fresh.reason };
}

function readStored() {
  try {
    const v = window.localStorage.getItem(MODE_KEY);
    return VALID_MODES.includes(v) ? v : DEFAULT_MODE;
  } catch {
    return DEFAULT_MODE;
  }
}

function writeStored(value) {
  try {
    window.localStorage.setItem(MODE_KEY, value);
  } catch {
    /* non-fatal, see theme.js's identical fallback */
  }
}

let mode = readStored();
const listeners = new Set();

function applyToDocument() {
  // data-mode drives theme.css's top-border-by-mode treatment -- written
  // explicitly (not omitted for the default) for the same reason
  // theme.js's data-theme is explicit: an always-present attribute is
  // something CSS and a screenshot both agree is really there.
  document.documentElement.setAttribute("data-mode", mode);
}
applyToDocument();

function notify() {
  applyToDocument();
  for (const l of listeners) l();
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getMode() {
  return mode;
}

// Never faked: paper/virtual refuse via the static MODE_BLOCKED_REASON
// check, same as before. Live is different NOW that its gate is async
// (useLiveReadiness) rather than a static flag -- this function can't run
// that check itself (it's synchronous, and a network round trip can't be
// forced into that shape), so it demands the CALLER's already-fetched
// readiness result as proof, and refuses "live" without one that says
// "ready". This is what stops a future caller from skipping the check by
// accident: setMode("live") alone (no second argument) fails closed,
// rather than silently succeeding the way it would if this function just
// trusted the caller to have checked. liveReadiness is verified fresh, not
// cached here, whether it was actually checked recently is still the
// caller's job (see useLiveReadiness's own comment on always re-checking
// on open) -- this only closes the "forgot to check at all" gap.
export function setMode(next, { liveReadiness } = {}) {
  if (!VALID_MODES.includes(next)) return false;
  if (MODE_BLOCKED_REASON[next]) return false;
  if (next === "live" && liveReadiness?.status !== "ready") return false;
  mode = next;
  writeStored(mode);
  notify();
  return true;
}

export function useMode() {
  return React.useSyncExternalStore(subscribe, getMode);
}
