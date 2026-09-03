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
    // mode is optional (omit for "every mode this user has", which is
    // never actually correct once virtual/live are real -- see the
    // 2026-08-30 AccountPanel.js incident: it called this with no mode at
    // all and silently kept showing paper's orders under every mode).
    // Bare-array return shape, unlike listPage below -- every caller here
    // (AccountPanel.js, Journal.js's trade picker) wants the plain list,
    // not X-Total-Count-based pagination.
    list: (mode) => request("GET", `/orders${mode ? `?mode=${mode}` : ""}`),
    // Filtered + paginated variant for the Logs screen.
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
    // The ONLY path that reaches a real broker (app/routers/orders.py's
    // confirm_live_order) -- a mode="live" submit only ever creates a
    // pending_confirmation row with zero broker contact; this is the
    // separate, explicit human action that actually dispatches it. See
    // components/LiveOrderConfirmModal.js for the one UI that calls this.
    confirm: (id) => request("POST", `/orders/${id}/confirm`),
    cancel: (id) => request("DELETE", `/orders/${id}`),
    brackets: {
      list: (mode) => request("GET", `/orders/brackets${mode ? `?mode=${mode}` : ""}`),
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
  virtual: {
    // Mirrors api.account()/api.equityCurve() almost exactly -- same
    // shape, just Mode.virtual's own Rs 1cr-starting book instead of
    // paper's (see app/routers/virtual.py's module docstring).
    account: () => request("GET", "/virtual/account"),
    equityCurve: () => request("GET", "/virtual/equity-curve"),
  },

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

  // Live-mode equivalent of `market.history` above, backed by a real
  // Angel One account instead of the simulated engine (see
  // app/routers/live_market.py). Deliberately a SEPARATE endpoint, not a
  // mode branch on GET /market/history -- paper/virtual keep using the
  // simulated engine's history unconditionally. Note the narrower
  // interval set: Angel One's candle API has no 5m/30m granularity (only
  // 1m/15m/1hr/1d), so this is NOT a drop-in replacement for
  // market.history's own interval keys -- see CandleChart.js's own
  // mode-dependent CANDLE_SECONDS_OPTIONS.
  live: {
    history: (symbol, interval, limit) =>
      request("GET", `/live/market/history?symbol=${encodeURIComponent(symbol)}&interval=${interval}${limit ? `&limit=${limit}` : ""}`),
    // Real Angel One options chain (app/routers/live_options.py) -- a
    // completely separate universe/shape from `options` above (221 real
    // underlyings vs. the synthetic 9-symbol chain, real bid/ask/LTP vs.
    // a BSM theoretical price, Angel One's own expiry string format vs.
    // an ISO date). Underlyings/expiries are local instrument-master
    // lookups (no broker call); chain is the one that actually touches
    // Angel One (a real, batched quote fetch).
    options: {
      underlyings: () => request("GET", "/live/options/underlyings"),
      expiries: (underlying) => request("GET", `/live/options/expiries?underlying=${encodeURIComponent(underlying)}`),
      chain: (underlying, expiry) =>
        request("GET", `/live/options/chain?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`),
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
// Shared by subscribeMarket and subscribeLiveMarket below -- identical
// reconnect/backoff discipline either way, the only difference is which
// path on WS_BASE they dial. Not exported: callers go through one of the
// two named wrappers (or subscribeMarketForMode) so the path a given
// caller ends up on is always explicit at the call site, not implied by
// which arguments happened to be passed to a generic function.
//
// maxConsecutiveFailures (undefined = retry forever, subscribeMarket's
// own behavior) exists ONLY because subscribeLiveMarket sets one -- an
// indefinite 800ms->8s reconnect loop against our OWN simulated backend
// is harmless (it's local, free, and every other part of this app already
// relies on it reconnecting no matter how long a dev restart takes), but
// the exact same loop against a REAL Angel One WebSocket is not: it's
// what turned a dropped connection into 1,539 reconnect attempts over 4+
// hours against a real account on 2026-08-28 (see the incident this
// constant exists to prevent a repeat of). "reconnecting" forever with no
// ceiling is a UX nicety for a free simulated feed and a real-account
// liability for a broker feed -- those are different enough risk profiles
// that this cannot be one shared default.
//
// failureCounter (optional {get,increment,reset}) is what makes the
// ceiling apply to the SYMBOL, not to whichever JS closure happens to be
// watching it. Without this, two independent subscribers to the same
// live symbol (StatusBar's heartbeat and whatever page is open both
// defaulting to the same instrument, say) would each get their OWN fresh
// 5 attempts -- caught in testing: a mocked-failure run against a single
// symbol produced 10 real connection attempts, not 5, because both
// subscribers ran their own private counter to the same ceiling. Passing
// a counter keyed by symbol (subscribeLiveMarket does this below) makes
// the second subscriber see the first one's failures already counted,
// so the aggregate against one real account+symbol is bounded by the
// ceiling itself, not multiplied by however many components happen to be
// watching it. Omitted entirely for subscribeMarket's sim case, which
// has no such shared-real-resource concern.
function _subscribeWs(wsPath, onTick, onStatusChange, { maxConsecutiveFailures, failureCounter } = {}) {
  let ws = null;
  let closedByCaller = false;
  let retryDelay = 800;
  let everConnected = false;
  let localFailures = 0;

  const getFailures = () => (failureCounter ? failureCounter.get() : localFailures);
  const bumpFailures = () => {
    if (failureCounter) failureCounter.increment();
    else localFailures += 1;
    return getFailures();
  };
  const resetFailures = () => {
    if (failureCounter) failureCounter.reset();
    else localFailures = 0;
  };

  const setStatus = (status) => onStatusChange && onStatusChange(status);

  function connect() {
    // Already exhausted by ANOTHER subscriber to this same symbol --
    // don't spend a real connection attempt just to immediately fail the
    // same way; report the same terminal state this symbol is already in.
    if (maxConsecutiveFailures && getFailures() >= maxConsecutiveFailures) {
      setStatus("disconnected");
      return;
    }
    setStatus(everConnected ? "reconnecting" : "connecting");
    ws = new WebSocket(`${WS_BASE}${wsPath}`);
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
      // A real, demonstrated success -- if this symbol's feed is
      // reachable again, any OTHER still-retrying (or already-tripped)
      // subscriber to it deserves a fresh chance too, not just this one.
      resetFailures();
      setStatus("live");
    };
    ws.onclose = () => {
      if (closedByCaller) return;
      const failures = bumpFailures();
      if (maxConsecutiveFailures && failures >= maxConsecutiveFailures) {
        // A genuine terminal state, not "reconnecting" and not "stale" --
        // this connection has stopped trying, on purpose, and will not
        // resume on its own. Callers must treat this as algoterminal's
        // ERROR state (five-states discipline: loading/empty/error/stale/
        // ready), distinct from staleness (which still implies "still
        // trying, just slow"). No further setTimeout is scheduled -- this
        // is the one exit from the reconnect loop that isn't the caller
        // unsubscribing.
        setStatus("disconnected");
        return;
      }
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

export function subscribeMarket(symbol, onTick, onStatusChange) {
  return _subscribeWs(`/ws/market/${symbol}`, onTick, onStatusChange);
}

// Real Angel One WebSocket connections per account are a finite, shared
// resource (rate limits, the account holder's own broker session) --
// unlike the simulated feed, this cannot be allowed to retry forever.
const LIVE_MAX_CONSECUTIVE_FAILURES = 5;

// Keyed by symbol, module-level (not per-call) -- this is exactly what
// makes the ceiling apply per SYMBOL rather than per subscriber; see
// _subscribeWs's own comment on the 10-attempts-not-5 bug this fixes.
// Never cleared except by a real reload (a fresh module evaluation) --
// deliberately outlives any one component unmounting, since navigating
// away and back within the same session must not hand a tripped symbol a
// new allowance.
const liveFailureCounts = new Map();
function _liveFailureCounter(symbol) {
  return {
    get: () => liveFailureCounts.get(symbol) || 0,
    increment: () => liveFailureCounts.set(symbol, (liveFailureCounts.get(symbol) || 0) + 1),
    reset: () => liveFailureCounts.set(symbol, 0),
  };
}

// Live-mode equivalent, backed by a real Angel One SmartWebSocketV2 feed
// (see app/routers/live_market.py). Same tick payload shape (type/symbol/
// price/best_bid/best_ask/bids/asks/sent_at) as subscribeMarket's own --
// best_bid/best_ask/bids/asks always come through as null/[] here (a live
// LTP-mode tick has no real order-book depth to report, the same "nothing
// fabricated" convention the simulated feed uses for a derived index's
// own missing book). If the connecting user has no complete broker
// credential, the server closes with code 4400 and a reason string
// immediately -- same reconnect-retry cycle as any other drop, up to the
// same failure ceiling as everything else here (a missing/incomplete
// credential is exactly the kind of failure that will NEVER clear on its
// own via retrying, so it should burn through the ceiling fast and stop,
// not spin for 4+ hours).
//
// The ceiling is enforced HERE, not left to the caller to opt into --
// same reasoning as setMode's live-readiness proof in mode.js: a safety
// property this important cannot depend on every future caller
// remembering to ask for it.
export function subscribeLiveMarket(symbol, onTick, onStatusChange) {
  return _subscribeWs(`/live/ws/market/${symbol}`, onTick, onStatusChange, {
    maxConsecutiveFailures: LIVE_MAX_CONSECUTIVE_FAILURES,
    failureCounter: _liveFailureCounter(symbol),
  });
}

// One switch point for "which feed does this symbol's live price come
// from right now" -- callers pass whatever useMode() gave them instead of
// each independently branching on "live" themselves, so paper/virtual/
// live can never drift into disagreeing about which feed backs a given
// mode.
export function subscribeMarketForMode(mode, symbol, onTick, onStatusChange) {
  return mode === "live"
    ? subscribeLiveMarket(symbol, onTick, onStatusChange)
    : subscribeMarket(symbol, onTick, onStatusChange);
}
