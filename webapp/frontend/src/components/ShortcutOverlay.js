import React from "react";
import { html } from "../html.js";
import { Modal } from "./Modal.js";
import { getAllBindings } from "../keyboard.js";

const KEY_LABEL = { esc: "Esc", "?": "?" };

function Kbd({ children }) {
  return html`<span class="kbd">${children}</span>`;
}

// "?" opens this -- but "?" only helps someone who already suspects
// shortcuts exist. The overlay itself is the fallback for someone who
// found it by accident or was told to press it; the REAL discovery path
// is the inline hints this same registry drives elsewhere (nav items,
// buttons), so a person graduates from clicking to typing without ever
// needing to open this at all.
export function ShortcutOverlay({ onClose }) {
  const bindings = getAllBindings();
  const groups = new Map();
  for (const b of bindings) {
    const group = b.group || "General";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(b);
  }

  return html`
    <${Modal} title="Keyboard Shortcuts" onClose=${onClose} size="md">
      ${Array.from(groups.entries()).map(([group, items]) => html`
        <div key=${group} style=${{ marginBottom: "16px" }}>
          <div class="indicator-picker-section" style=${{ marginTop: 0 }}>${group}</div>
          ${items.map((b) => html`
            <div key=${b.chord} class="row hairline">
              <span style=${{ color: "var(--text-dim)" }}>${b.description || b.chord}</span>
              <span style=${{ display: "flex", gap: "4px" }}>
                ${b.chord.split(" ").map((k, i) => html`<${Kbd} key=${i}>${KEY_LABEL[k] || k.toUpperCase()}<//>`)}
              </span>
            </div>
          `)}
        </div>
      `)}
    <//>
  `;
}
