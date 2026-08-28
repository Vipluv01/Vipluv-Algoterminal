// Theme (dark/light) and density (comfortable/compact) state -- applied as
// data-theme/data-density attributes on <html> (theme.css's [data-theme]
// and [data-density] selectors read them from there), persisted to
// localStorage, and exposed as hooks so any component (StatusBar's own
// toggle included) can read or flip either one and have every other
// subscriber re-render in step -- same external-store shape as clock.js's
// shared ticking clock, for the same reason: one source of truth, not a
// copy of the setting re-derived independently in each component.
import React from "react";

const THEME_KEY = "algoterminal:theme";
const DENSITY_KEY = "algoterminal:density";

// Dark-first: this is a trading terminal, and a bright white page is both
// a jarring default for a screen meant to be read for hours and (via
// prefers-color-scheme not being checked here at all) not something this
// app infers from the OS -- an explicit choice via the toggle is the only
// way to light mode.
const DEFAULT_THEME = "dark";
const DEFAULT_DENSITY = "comfortable";

function readStored(key, fallback, allowed) {
  try {
    const v = window.localStorage.getItem(key);
    return allowed.includes(v) ? v : fallback;
  } catch {
    // localStorage can throw (Safari private mode, disabled storage) --
    // fall back silently rather than take the whole app down over a
    // cosmetic preference.
    return fallback;
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* see readStored -- storage failures here are non-fatal */
  }
}

let theme = readStored(THEME_KEY, DEFAULT_THEME, ["dark", "light"]);
let density = readStored(DENSITY_KEY, DEFAULT_DENSITY, ["comfortable", "compact"]);
const listeners = new Set();

function applyToDocument() {
  // data-theme="dark" is written explicitly (not omitted) even though
  // dark is theme.css's un-attributed default -- an explicit attribute
  // means a future prefers-color-scheme media-query addition can't
  // silently reinterpret an "absent" attribute as "follow the OS"; the
  // user's actual stored choice is always physically present in the DOM.
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-density", density);
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

export function getTheme() {
  return theme;
}

export function setTheme(next) {
  if (next !== "dark" && next !== "light") return;
  theme = next;
  writeStored(THEME_KEY, theme);
  notify();
}

export function toggleTheme() {
  setTheme(theme === "dark" ? "light" : "dark");
}

export function getDensity() {
  return density;
}

export function setDensity(next) {
  if (next !== "comfortable" && next !== "compact") return;
  density = next;
  writeStored(DENSITY_KEY, density);
  notify();
}

export function useTheme() {
  return React.useSyncExternalStore(subscribe, getTheme);
}

export function useDensity() {
  return React.useSyncExternalStore(subscribe, getDensity);
}
