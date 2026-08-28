// Trading mode (paper/virtual/live) as real app state -- previously two
// separate hardcoded string literals ("PAPER MODE" in App.js's nav badge,
// "MODE: PAPER" in StatusBar.js) that could never agree or disagree with
// each other because neither one was actually state; both now read from
// here. Same external-store shape as theme.js's theme/density (one
// source of truth, persisted, subscribable), for the same reason.
import React from "react";

const MODE_KEY = "algoterminal:mode";
const VALID_MODES = ["paper", "virtual", "live"];
const DEFAULT_MODE = "paper";

// Neither virtual nor live has anything real behind it yet (no broker
// credentials flow, no virtual-capital ledger) -- exposed here, not
// hardcoded per-component, so every place that needs to know "can this
// mode actually be selected right now" (the switcher, Accounts' chips)
// asks the same question and gets the same answer instead of drifting.
export const MODE_BLOCKED_REASON = {
  paper: null,
  virtual: "Virtual capital tracking isn't implemented yet — Phase 7.",
  live: "Requires connected broker credentials — Phase 7.",
};

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

// Never faked: a mode with a MODE_BLOCKED_REASON refuses to set. There is
// no "looks selected but silently behaves as paper" state reachable
// through this function -- the caller (ModeSwitcher) is expected to check
// MODE_BLOCKED_REASON itself and never call this for a blocked mode, but
// this is the actual enforcement point, not just the UI's good behavior.
export function setMode(next) {
  if (!VALID_MODES.includes(next) || MODE_BLOCKED_REASON[next]) return false;
  mode = next;
  writeStored(mode);
  notify();
  return true;
}

export function useMode() {
  return React.useSyncExternalStore(subscribe, getMode);
}
