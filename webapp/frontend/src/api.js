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

// Same contract as request(), but also hands back the response's
// X-Total-Count header -- GET /orders is paginated server-side (limit/
// offset) and reports the true pre-pagination row count there rather than
// in the body, so a caller rendering "page 2 of 14" needs it without
// changing what request()'s existing callers get back.
async function requestWithTotal(method, path) {
  const res = await fetch(`${API_BASE}${path}`, { method });
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
  const items = await res.json();
  const totalHeader = res.headers.get("X-Total-Count");
  return { items, total: totalHeader !== null ? Number(totalHeader) : items.length };
}

export const api = {
  symbols: () => request("GET", "/symbols"),

  orders: {
    list: () => request("GET", "/orders"),
    // Filtered + paginated variant for the Logs screen -- `list()` above
    // stays untouched (bare array, no params) since AccountPanel.js and
    // Journal.js's trade picker both call it that way already.
    listPage: ({ mode, status, symbol, strategy, dateFrom, dateTo, limit, offset } = {}) => {
      const params = new URLSearchParams();
      if (mode) params.set("mode", mode);
      if (status) params.set("status", status);
      if (symbol) params.set("symbol", symbol);
      if (strategy) params.set("strategy", strategy);
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo) params.set("date_to", dateTo);
      if (limit != null) params.set("limit", String(limit));
      if (offset != null) params.set("offset", String(offset));
      const qs = params.toString();
      return requestWithTotal("GET", `/orders${qs ? `?${qs}` : ""}`);
    },
    submit: (body) => request("POST", "/orders", body),
    cancel: (id) => request("DELETE", `/orders/${id}`),
    brackets: {
      list: () => request("GET", "/orders/brackets"),
      cancel: (id) => request("DELETE", `/orders/brackets/${id}`),
    },
  },

  account: () => request("GET", "/account"),
  equityCurve: () => request("GET", "/account/equity-curve"),

  risk: {
    get: () => request("GET", "/risk"),
    update: (body) => request("PUT", "/risk", body),
    resetHalt: () => request("POST", "/risk/reset-halt"),
  },

  strategies: {
    list: () => request("GET", "/strategies"),
    allocations: () => request("GET", "/strategies/allocations"),
    setAllocation: (key, body) => request("PUT", `/strategies/allocations/${key}`, body),
  },

  dashboard: {
    stats: () => request("GET", "/dashboard/stats"),
    calendar: () => request("GET", "/dashboard/calendar"),
    // Notes now live entirely under /journal -- /dashboard/notes was
    // removed server-side (see app/routers/journal.py's module docstring),
    // not duplicated, so there is exactly one place notes are read or
    // written. Kept here only as the `journal` key below, not re-added
    // under `dashboard`, so nothing can silently point at the dead route.
  },

  journal: {
    list: () => request("GET", "/journal/notes"),
    create: (body) => request("POST", "/journal/notes", body),
    delete: (id) => request("DELETE", `/journal/notes/${id}`),
  },

  // STORE-ONLY: the server never returns a decrypted secret from any of
  // these (see app/routers/vault.py's module docstring) -- only last-4 +
  // a rotation timestamp. Nothing on the frontend should ever expect a
  // full api_key/api_secret back from a response here.
  vault: {
    get: () => request("GET", "/vault/credential"),
    put: (body) => request("POST", "/vault/credential", body),
    delete: () => request("DELETE", "/vault/credential"),
  },

  // `since` is an ISO datetime string (or omitted for "no delta yet" --
  // every entry's pnl_delta/rank_delta come back null, per LeaderboardOut).
  leaderboard: (since) => request("GET", `/leaderboard${since ? `?since=${encodeURIComponent(since)}` : ""}`),

  portfolio: {
    attribution: () => request("GET", "/portfolio/attribution"),
    // Deliberately "realized-pnl-curve" / `realized_pnl`, not "equity-curve"
    // / `equity" -- see app/routers/portfolio.py's RealizedPnlPointOut
    // docstring. This is a REALIZED-only walk (for clean Brinson attribution
    // periods); GET /account/equity-curve (api.equityCurve() above) is the
    // genuine mark-to-market curve. Do not rename either back to "equity"
    // on this side, or the two charts read as disagreeing again.
    realizedPnlCurve: () => request("GET", "/portfolio/realized-pnl-curve"),
    subAccounts: () => request("GET", "/portfolio/sub-accounts"),
  },

  options: {
    expiries: () => request("GET", "/options/expiries"),
    chain: (underlying, expiry) =>
      request("GET", `/options/chain?underlying=${encodeURIComponent(underlying)}${expiry ? `&expiry=${encodeURIComponent(expiry)}` : ""}`),
    // Response always carries execution_notice verbatim (see
    // app/options/execution.py's EXECUTION_NOTICE) -- every OTHER order in
    // this app is a real match from the Go engine; this is model-priced
    // synthetic execution, and that has to travel with every single fill,
    // not just live on this screen's own static banner.
    submitOrder: (body) => request("POST", "/options/orders", body),
    greeks: () => request("GET", "/options/greeks"),
  },

  telemetry: {
    // null (not a fabricated 0) until the first real order submit -- see
    // app/routers/telemetry.py's LatencyOut docstring. StatusBar renders
    // "-" for that case rather than guessing.
    latency: () => request("GET", "/telemetry/latency"),
  },

  market: {
    // Seeds a chart's history in one request -- see app/routers/market.py's
    // module docstring for why this exists (without it, a freshly-loaded
    // chart is blank until enough live ticks arrive to fill one bar, which
    // at 1hr candles is up to an hour). `interval` matches CandleChart.js's
    // own CANDLE_SECONDS_OPTIONS keys.
    history: (symbol, interval, limit) =>
      request("GET", `/market/history?symbol=${encodeURIComponent(symbol)}&interval=${interval}${limit ? `&limit=${limit}` : ""}`),
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
//
// onStatusChange is optional and backward-compatible: every existing
// caller (Ticker.js, Terminal.js, Charts.js, ManualTrade.js) passes only
// (symbol, onTick) and is unaffected. When supplied, it's called with
// "connecting" (before the very first open), "live" (socket open), or
// "reconnecting" (a previously-open socket just dropped and a retry is
// scheduled) -- StatusBar.js is the one caller that currently uses this,
// to drive its connection indicator. There's no "offline" event fired
// from here: that's derived by the CALLER from elapsed time since the
// last "live" status (via clock.js's useStaleness), the same staleness
// primitive prices use -- a connection that's been "reconnecting" for
// 3+ seconds and one that's merely mid-retry look identical from inside
// this function, so "how long has it actually been down" belongs with
// whoever is watching the clock, not duplicated here.
export function subscribeMarket(symbol, onTick, onStatusChange) {
  let ws = null;
  let closedByCaller = false;
  let retryDelay = 800;
  let everConnected = false;

  const setStatus = (status) => onStatusChange && onStatusChange(status);

  function connect() {
    setStatus(everConnected ? "reconnecting" : "connecting");
    ws = new WebSocket(`${WS_BASE}/ws/market/${symbol}`);
    ws.onmessage = (ev) => {
      try {
        onTick(JSON.parse(ev.data));
      } catch {
        /* malformed frame -- drop it, next tick will arrive shortly */
      }
    };
    ws.onopen = () => {
      retryDelay = 800;
      everConnected = true;
      setStatus("live");
    };
    ws.onclose = () => {
      if (closedByCaller) return;
      setStatus("reconnecting");
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
