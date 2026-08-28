// Shared, polled trading_halted status -- so the halt banner can be
// GLOBAL (rendered once, in App.js, across every screen) rather than
// each page fetching GET /risk independently and only the Risk page
// itself knowing whether trading is halted. Same external-store shape as
// theme.js/mode.js, but backed by a periodic real fetch instead of purely
// local state, since this value can change from a SERVER-side circuit
// breaker trip, not just a local action.
import React from "react";
import { api } from "./api.js";

const POLL_MS = 10000;

let halted = null; // null = not yet known, true/false once a poll succeeds
const listeners = new Set();
let pollId = null;

async function poll() {
  try {
    const risk = await api.risk.get();
    halted = risk.trading_halted;
  } catch {
    // A transient fetch failure leaves the last-known value alone --
    // flipping a real halt banner off because one poll timed out would
    // be worse than a banner that's a few seconds stale.
  }
  notify();
}

function notify() {
  for (const l of listeners) l();
}

function subscribe(listener) {
  listeners.add(listener);
  if (pollId === null) {
    poll();
    pollId = setInterval(poll, POLL_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && pollId !== null) {
      clearInterval(pollId);
      pollId = null;
    }
  };
}

export function useTradingHalted() {
  return React.useSyncExternalStore(subscribe, () => halted);
}

// Called right after a successful PUT /risk or POST /risk/reset-halt so
// the banner (and anything else watching) updates immediately instead of
// waiting up to POLL_MS for the next scheduled poll.
export function refreshRiskStatus() {
  return poll();
}
