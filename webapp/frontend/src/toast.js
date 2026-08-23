import React from "react";
import { html } from "./html.js";

const ToastContext = React.createContext(() => {});

export function ToastProvider({ children }) {
  const [toasts, setToasts] = React.useState([]);

  const push = React.useCallback((message, kind = "ok") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  }, []);

  return html`
    <${ToastContext.Provider} value=${push}>
      ${children}
      <div style=${{ position: "fixed", bottom: "20px", right: "20px", zIndex: 100, display: "flex", flexDirection: "column", gap: "10px", alignItems: "flex-end" }}>
        ${toasts.map((t) => html`
          <div key=${t.id} class=${`toast ${t.kind}`} style=${{ position: "static" }}>
            ${t.message}
          </div>
        `)}
      </div>
    <//>
  `;
}

export function useToast() {
  return React.useContext(ToastContext);
}
