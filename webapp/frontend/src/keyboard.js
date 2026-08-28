// App-wide keyboard shortcut registry -- ONE document-level keydown
// listener for the whole app (see ensureListener below), not one per
// component. register()/useShortcuts() are the only ways in; every
// caller adds entries to the same shared registry rather than attaching
// its own listener, so N components registering shortcuts still costs
// exactly one DOM listener.
import React from "react";

const CHORD_TIMEOUT_MS = 800;

// chord string ("g d", "b", "esc", "?") -> { handler, description, group }
const registry = new Map();
const pendingListeners = new Set();

let pendingChord = null; // array of keys typed so far in an unresolved chord, or null
let pendingTimeoutId = null;
let listenerAttached = false;

function normalizeKey(e) {
  if (e.key === "Escape") return "esc";
  return e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase();
}

function isTypingContext(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable === true;
}

function exactMatch(keys) {
  return registry.get(keys.join(" "));
}

// True if `keys` is a real prefix of some LONGER registered chord (so we
// should keep waiting for the next key rather than give up).
function hasLongerMatch(keys) {
  const prefix = keys.join(" ") + " ";
  for (const chord of registry.keys()) {
    if (chord.startsWith(prefix)) return true;
  }
  return false;
}

function setPending(keys) {
  pendingChord = keys;
  clearTimeout(pendingTimeoutId);
  pendingTimeoutId = setTimeout(clearPending, CHORD_TIMEOUT_MS);
  notifyPending();
}

function clearPending() {
  pendingChord = null;
  clearTimeout(pendingTimeoutId);
  pendingTimeoutId = null;
  notifyPending();
}

function notifyPending() {
  for (const l of pendingListeners) l(pendingChord);
}

function tryStartFresh(e, key) {
  const exact = exactMatch([key]);
  if (exact) {
    e.preventDefault();
    clearPending();
    exact.handler(e);
    return;
  }
  if (hasLongerMatch([key])) {
    e.preventDefault();
    setPending([key]);
  }
}

function onKeyDown(e) {
  // Never hijack a browser/OS chord (Cmd+R, Ctrl+T, Alt+Tab, ...) -- only
  // bare keys and Shift-modified ones (needed for "?") are ours to claim.
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (isTypingContext(e.target)) return;

  const key = normalizeKey(e);

  if (!pendingChord) {
    tryStartFresh(e, key);
    return;
  }

  const keys = [...pendingChord, key];
  const exact = exactMatch(keys);
  if (exact) {
    e.preventDefault();
    clearPending();
    exact.handler(e);
    return;
  }
  if (hasLongerMatch(keys)) {
    e.preventDefault();
    setPending(keys);
    return;
  }
  // This key doesn't continue the pending chord -- give up on it and
  // re-evaluate the SAME keystroke as a fresh, single-key start (so
  // "g" then, say, "x" doesn't eat the "x" if "x" is itself bound).
  clearPending();
  tryStartFresh(e, key);
}

function ensureListener() {
  if (listenerAttached) return;
  document.addEventListener("keydown", onKeyDown);
  listenerAttached = true;
}

// Registers `handler` under `chord` (space-separated key sequence, e.g.
// "g d", or a single key "b" / "esc" / "?"). Returns an unregister
// function. meta.description/meta.group are for ShortcutOverlay's listing
// only -- they don't affect matching.
export function register(chord, handler, meta = {}) {
  ensureListener();
  registry.set(chord, { handler, description: meta.description, group: meta.group });
  return () => unregister(chord);
}

export function unregister(chord) {
  registry.delete(chord);
}

export function getAllBindings() {
  return Array.from(registry.entries()).map(([chord, meta]) => ({ chord, ...meta }));
}

export function subscribePendingChord(listener) {
  pendingListeners.add(listener);
  return () => pendingListeners.delete(listener);
}

export function getPendingChord() {
  return pendingChord;
}

// React hook: registers every binding in `bindings` ({chord, handler,
// description, group}[]) on mount, unregisters on unmount -- the
// per-component-lifetime half of "ONE global listener, many registered
// bindings." `bindings` should be a stable array (useMemo it, or define
// it as a module-level constant) -- a new array identity every render
// would re-register on every render, which still works but churns the
// registry for no reason.
export function useShortcuts(bindings) {
  React.useEffect(() => {
    const unsubs = bindings.map((b) => register(b.chord, b.handler, { description: b.description, group: b.group }));
    return () => unsubs.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindings]);
}

// Live-updating pending-chord string for StatusBar's hint ("g…" while
// waiting for the second key of a chord).
export function usePendingChord() {
  const [chord, setChord] = React.useState(getPendingChord());
  React.useEffect(() => subscribePendingChord(setChord), []);
  return chord;
}
