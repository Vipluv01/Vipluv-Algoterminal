import React from "react";
import { html } from "../html.js";

const FOCUSABLE_SELECTOR =
  "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

// A real focus trap, not just a visual overlay: Escape and backdrop-click
// close (matching CandleChart's pre-existing IndicatorPicker, which this
// generalizes), Tab/Shift+Tab wrap WITHIN the modal instead of escaping to
// whatever's behind it, and focus returns to whichever element actually
// opened the modal on close -- without that last part, a keyboard user who
// opened a modal from a toolbar button gets dropped back at the top of the
// page instead of where they were.
export function Modal({ title, onClose, children, size = "md" }) {
  const modalRef = React.useRef(null);
  const triggerRef = React.useRef(null);

  React.useEffect(() => {
    triggerRef.current = document.activeElement;

    const node = modalRef.current;
    const focusables = () => Array.from(node.querySelectorAll(FOCUSABLE_SELECTOR));
    const first = focusables()[0];
    (first || node).focus();

    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const els = focusables();
      if (!els.length) {
        e.preventDefault();
        return;
      }
      const firstEl = els[0];
      const lastEl = els[els.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      // The trigger can have unmounted while the modal was open (e.g. its
      // own parent re-rendered) -- restoring focus to a detached node is a
      // silent no-op in every browser, not an error, so this doesn't need
      // an isConnected guard to be safe, just correct: it falls back to
      // whatever the browser already does (nothing) rather than throwing.
      if (triggerRef.current && document.body.contains(triggerRef.current)) {
        triggerRef.current.focus();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return html`
    <div class="modal-backdrop" onClick=${onClose}>
      <div
        ref=${modalRef}
        class=${`modal modal-${size}`}
        role="dialog"
        aria-modal="true"
        aria-label=${title || "Dialog"}
        tabindex="-1"
        onClick=${(e) => e.stopPropagation()}
      >
        ${title && html`
          <div class="modal-header">
            <span>${title}</span>
            <button class="btn btn-sm btn-ghost" onClick=${onClose} aria-label="Close">✕</button>
          </div>
        `}
        <div class="modal-body">${children}</div>
      </div>
    </div>
  `;
}
