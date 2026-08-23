// Thin REST + WebSocket client for algoterminal's FastAPI backend.
// No auth headers yet -- app/auth.py's Phase-1 placeholder resolves every
// request to one fixed dev user regardless, so there's nothing to attach
// here until Phase 3.

// Local dev runs the frontend (nocache_server.py, :5173) and the backend
// (uvicorn, :8001) as two separate processes, so it needs an explicit
// cross-origin base. In production (Render), app/main.py serves both from
// the SAME process/port -- same-origin, so a relative "" base is correct
// there and must NOT be hardcoded to localhost. window.__ALGOTERMINAL_API__
// still overrides both if ever needed.
const IS_LOCAL_DEV_SERVER = window.location.port === "5173";
export const API_BASE = window.__ALGOTERMINAL_API__ ?? (IS_LOCAL_DEV_SERVER ? "http://localhost:8001" : "");
// WebSocket() requires an absolute ws://|wss:// URL, unlike fetch, which
// resolves a relative path against the page's own origin automatically --
// so an empty (same-origin) API_BASE needs its own explicit ws(s):// + host.
const WS_BASE = API_BASE
  ? API_BASE.replace(/^http/, "ws")
  : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;

async function request(method, path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errBody = await res.json();
      detail = errBody.detail || detail;
    } catch {
      /* response wasn't JSON -- fall back to statusText */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  symbols: () => request("GET", "/symbols"),

  orders: {
    list: () => request("GET", "/orders"),
    submit: (body) => request("POST", "/orders", body),
    cancel: (id) => request("DELETE", `/orders/${id}`),
    brackets: {
      list: () => request("GET", "/orders/brackets"),
      cancel: (id) => request("DELETE", `/orders/brackets/${id}`),
    },
  },

  account: () => request("GET", "/account"),

  risk: () => request("GET", "/risk"),

  strategies: {
    list: () => request("GET", "/strategies"),
    allocations: () => request("GET", "/strategies/allocations"),
    setAllocation: (key, body) => request("PUT", `/strategies/allocations/${key}`, body),
  },

  dashboard: {
    stats: () => request("GET", "/dashboard/stats"),
    calendar: () => request("GET", "/dashboard/calendar"),
    notes: {
      list: () => request("GET", "/dashboard/notes"),
      create: (text) => request("POST", "/dashboard/notes", { text }),
      delete: (id) => request("DELETE", `/dashboard/notes/${id}`),
    },
  },

  pairs: {
    overview: () => request("GET", "/pairs/overview"),
    analytics: () => request("GET", "/pairs/analytics"),
    close: () => request("POST", "/pairs/close"),
  },

  optimizer: () => request("GET", "/optimizer"),
};

// Opens a live market WebSocket for one symbol. Returns an unsubscribe
// function. Reconnects automatically on drop (matching the reconnect
// discipline the bourse demo's own frontend already uses), with a short
// backoff so a backend restart doesn't get hammered by instant retries.
export function subscribeMarket(symbol, onTick) {
  let ws = null;
  let closedByCaller = false;
  let retryDelay = 800;

  function connect() {
    ws = new WebSocket(`${WS_BASE}/ws/market/${symbol}`);
    ws.onmessage = (ev) => {
      try {
        onTick(JSON.parse(ev.data));
      } catch {
        /* malformed frame -- drop it, next tick will arrive shortly */
      }
    };
    ws.onopen = () => { retryDelay = 800; };
    ws.onclose = () => {
      if (closedByCaller) return;
      setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.5, 8000);
    };
    ws.onerror = () => ws.close();
  }
  connect();

  return () => {
    closedByCaller = true;
    ws && ws.close();
  };
}
