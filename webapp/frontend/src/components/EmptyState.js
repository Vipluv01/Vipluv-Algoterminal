import React from "react";
import { html } from "../html.js";

// One sentence plus the next action -- an empty panel with no action is a
// dead end (the viewer knows nothing is here, but not what to do about
// it). actionLabel/onAction are both optional together: a state that's
// empty for a reason with no real next step (e.g. "no orders yet today"
// on a fresh account) can render the message alone rather than force a
// button that has nothing useful to do.
export function EmptyState({ message, actionLabel, onAction, icon = null }) {
  return html`
    <div class="empty-state">
      ${icon}
      <div class="empty-state-message">${message}</div>
      ${actionLabel && onAction && html`
        <button class="btn btn-sm btn-ghost" onClick=${onAction}>${actionLabel}</button>
      `}
    </div>
  `;
}
