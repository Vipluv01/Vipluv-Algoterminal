// Cmd+K / "/" command palette -- quick navigation, symbol jump, and mode
// switching from anywhere in the app. Built on Modal.js for its real
// focus-trap/Escape/focus-restore behavior (not reinvented here), styled
// as a top-anchored search rather than Modal's default centered dialog.
//
// Cmd+K needs its OWN listener, deliberately outside src/keyboard.js's
// shared registry: that registry explicitly bails out on any keydown
// with a modifier held (metaKey/ctrlKey/altKey) -- a deliberate design
// choice to never fight a real OS/browser shortcut with a plain-chord
// system built for sequences like "g d". Cmd+K is a genuinely different
// interaction (a held-modifier shortcut, not a chord), so it gets its
// own narrow listener rather than loosening that registry's guard for
// everyone. "/" DOES fit the existing chord system (no modifier), so
// that one goes through register()/useShortcuts normally, in App.js.
import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { Modal } from "./Modal.js";
import { NAV_ITEMS } from "../navItems.js";
import { setMode, MODE_BLOCKED_REASON, useLiveReadiness } from "../mode.js";

let openSetter = null;

export function openCommandPalette() {
  openSetter && openSetter(true);
}

function useGlobalCmdK() {
  React.useEffect(() => {
    function onKeyDown(e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openCommandPalette();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}

function fuzzyMatch(query, text) {
  if (!query) return true;
  return text.toLowerCase().includes(query.toLowerCase());
}

export function CommandPalette() {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [symbols, setSymbols] = React.useState([]);
  const [selected, setSelected] = React.useState(0);
  const inputRef = React.useRef(null);
  const liveReadiness = useLiveReadiness(open);

  React.useEffect(() => { openSetter = setOpen; return () => { openSetter = null; }; }, []);
  useGlobalCmdK();

  React.useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelected(0);
    api.symbols().then(setSymbols).catch(() => setSymbols([]));
    // Modal.js already focuses its first focusable element on open, but
    // that races this input existing yet on the very first paint --
    // one more explicit focus after mount is cheap insurance, not a
    // workaround for something actually broken in Modal itself.
    const id = setTimeout(() => inputRef.current && inputRef.current.focus(), 0);
    return () => clearTimeout(id);
  }, [open]);

  if (!open) return null;

  const navMatches = NAV_ITEMS.filter((n) => fuzzyMatch(query, n.label));
  const symbolMatches = query
    ? symbols.filter((s) => fuzzyMatch(query, s.symbol)).slice(0, 6)
    : [];
  const modeMatches = ["paper", "virtual", "live"].filter((m) => fuzzyMatch(query, `${m} mode`));

  const items = [
    ...navMatches.map((n) => ({ kind: "nav", key: n.hash, label: n.label, sub: n.hash, action: () => { window.location.hash = n.hash; setOpen(false); } })),
    ...symbolMatches.map((s) => ({ kind: "symbol", key: s.symbol, label: s.symbol, sub: `₹${s.reference_price?.toFixed(2) ?? "—"}`, action: () => { window.location.hash = "#/terminal"; setOpen(false); } })),
    ...modeMatches.map((m) => ({
      kind: "mode", key: m, label: `Switch to ${m} mode`,
      sub: m === "live" ? (liveReadiness.status === "ready" ? "type LIVE to confirm on the switcher" : liveReadiness.reason || "checking…") : MODE_BLOCKED_REASON[m] || "",
      disabled: m === "live",
      action: () => { if (m !== "live") { setMode(m); setOpen(false); } },
    })),
  ];

  function onKeyDown(e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setSelected((i) => Math.min(i + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSelected((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); const item = items[selected]; if (item && !item.disabled) item.action(); }
  }

  return html`
    <${Modal} onClose=${() => setOpen(false)} size="md">
      <div onKeyDown=${onKeyDown}>
        <input
          ref=${inputRef}
          class="input"
          style=${{ fontSize: "15px", padding: "12px 14px", marginBottom: "10px" }}
          placeholder="Jump to a page, symbol, or mode…"
          value=${query}
          onInput=${(e) => { setQuery(e.target.value); setSelected(0); }}
        />
        <div style=${{ maxHeight: "360px", overflowY: "auto" }}>
          ${items.length === 0 && html`
            <div style=${{ padding: "20px 8px", color: "var(--text-faint)", fontSize: "13px", textAlign: "center" }}>No matches</div>
          `}
          ${items.map((item, i) => html`
            <div
              key=${`${item.kind}-${item.key}`}
              onClick=${() => !item.disabled && item.action()}
              onMouseEnter=${() => setSelected(i)}
              style=${{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "9px 10px", borderRadius: "6px", cursor: item.disabled ? "not-allowed" : "pointer",
                opacity: item.disabled ? 0.5 : 1,
                background: i === selected ? "var(--surface-hover)" : "transparent",
              }}
            >
              <span style=${{ fontSize: "13.5px", fontWeight: 600 }}>
                <span class="mono" style=${{ color: "var(--text-faint)", fontSize: "10px", marginRight: "8px", textTransform: "uppercase" }}>${item.kind}</span>
                ${item.label}
              </span>
              <span class="mono" style=${{ fontSize: "11.5px", color: "var(--text-faint)" }}>${item.sub}</span>
            </div>
          `)}
        </div>
      </div>
    <//>
  `;
}
