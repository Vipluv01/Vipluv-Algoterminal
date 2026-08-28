// A single shared "now", ticking once a second, that any number of
// components can subscribe to -- NOT one setInterval per component. Backs
// useStaleness() below (and the connection indicator's own "how long since
// the last successful tick" derivation in StatusBar.js), so a page with
// dozens of price cells each checking their own staleness costs exactly one
// timer for the whole tree, not one per cell.
import React from "react";

const TICK_MS = 1000;
// If the WebSocket drops, a price must visibly go stale within roughly this
// long -- not silently freeze at the last tick while looking current. 3000ms
// matches "~3 seconds" because a frozen-but-live-looking price is a real
// safety concern for a trading terminal, not a cosmetic one.
export const DEFAULT_STALE_THRESHOLD_MS = 3000;

const listeners = new Set();
let now = Date.now();
let intervalId = null;

function tick() {
  now = Date.now();
  for (const listener of listeners) listener();
}

function subscribeClock(listener) {
  listeners.add(listener);
  if (intervalId === null) intervalId = setInterval(tick, TICK_MS);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && intervalId !== null) {
      clearInterval(intervalId);
      intervalId = null;
    }
  };
}

function getClockNow() {
  return now;
}

// The shared clock as a hook -- every component calling this re-renders
// on the SAME once-a-second tick, driven by the ONE interval above.
export function useNow() {
  return React.useSyncExternalStore(subscribeClock, getClockNow);
}

// Given a lastUpdated timestamp (ms epoch, or null/undefined if nothing has
// ever arrived yet) and a threshold, returns "fresh" or "stale". A
// lastUpdated of null/undefined is treated as stale -- "never received
// data" is not "fresh", it just hasn't been checked yet.
export function useStaleness(lastUpdated, thresholdMs = DEFAULT_STALE_THRESHOLD_MS) {
  const now = useNow();
  if (lastUpdated === null || lastUpdated === undefined) return "stale";
  return now - lastUpdated > thresholdMs ? "stale" : "fresh";
}

// Small formatting helper for the "data age" a stale surface should show
// next to its last-known value (see theme.css's .is-stale, EmptyState.js's
// sibling components, and StatusBar's own "last update" readout).
export function formatAge(lastUpdated, nowMs = Date.now()) {
  if (lastUpdated === null || lastUpdated === undefined) return "never";
  const seconds = Math.max(0, Math.floor((nowMs - lastUpdated) / 1000));
  if (seconds < 1) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}
