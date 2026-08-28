import React from "react";
import { html } from "../html.js";
import { api, subscribeMarketForMode } from "../api.js";
import { DEFAULT_STALE_THRESHOLD_MS, formatAge, useStaleness } from "../clock.js";
import { useDensity, setDensity, useTheme, toggleTheme } from "../theme.js";
import { usePendingChord } from "../keyboard.js";
import { useMode } from "../mode.js";
import { dash } from "../format.js";

const LATENCY_POLL_MS = 5000;

// GET /telemetry/latency is an aggregate over recent order submits, not a
// per-tick value -- polling it is the right shape (matches how the rest of
// this app treats slow-moving aggregates, e.g. Risk.js), unlike the WS
// delivery delta below which updates on every single tick for free.
function useOrderLatency() {
  const [latency, setLatency] = React.useState(undefined); // undefined = not fetched yet

  React.useEffect(() => {
    let cancelled = false;
    const load = () => api.telemetry.latency().then((v) => { if (!cancelled) setLatency(v); }).catch(() => {});
    load();
    const id = setInterval(load, LATENCY_POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  return latency; // undefined (not fetched) or null (no samples yet) both render as "-"
}

function useClock() {
  const [now, setNow] = React.useState(new Date());
  React.useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

// A dedicated, standing subscription purely for connection health -- not
// tied to whatever symbol(s) the current page happens to be watching (a
// page's own subscribeMarket calls come and go with navigation; the
// StatusBar's connection indicator should reflect real backend
// reachability regardless of which page is open). ICICIBANK matches the
// reference symbol already used as the canonical default throughout this
// codebase (pairs_cointegration's validated pair, scripts/run_backtests.py's
// CANONICAL_SYMBOL, ...), not a special choice made here.
const HEARTBEAT_SYMBOL = "ICICIBANK";

// connecting|live|reconnecting -- raw states subscribeMarket itself
// reports. "offline" is NOT one of them: it's derived below from how long
// it's actually been since the last successful tick (via useStaleness,
// the same primitive a stale PRICE cell uses) -- a connection that
// dropped half a second ago and one that's been down for a minute both
// report "reconnecting" from api.js's own point of view, and only the
// elapsed time tells them apart.
function useConnectionStatus(mode) {
  const [wsStatus, setWsStatus] = React.useState("connecting");
  const [lastTickAt, setLastTickAt] = React.useState(null);
  // Client-computed (Date.now() - tick.sent_at), per tick -- see
  // app/routers/market_ws.py's _tick_payload docstring on why this is
  // measured here rather than the server guessing a delay it can't see.
  // null until the first tick ever carries a sent_at to diff against.
  const [wsDeltaMs, setWsDeltaMs] = React.useState(null);

  React.useEffect(() => {
    // In live mode this heartbeat is Angel One's own feed, not the
    // simulated engine -- "SYSTEM: LIVE" should mean the trader's ACTUAL
    // broker connection is healthy, not that an unrelated simulation is
    // still ticking while the real feed they'd be trading against is down.
    setWsStatus("connecting");
    setLastTickAt(null);
    const unsub = subscribeMarketForMode(
      mode,
      HEARTBEAT_SYMBOL,
      (tick) => {
        setLastTickAt(Date.now());
        if (typeof tick?.sent_at === "number") setWsDeltaMs(Date.now() - tick.sent_at);
      },
      setWsStatus,
    );
    return unsub;
  }, [mode]);

  const freshness = useStaleness(lastTickAt, DEFAULT_STALE_THRESHOLD_MS);

  let status;
  if (wsStatus === "live" && freshness === "fresh") status = "live";
  else if (lastTickAt === null) status = wsStatus; // never yet connected -- "connecting", no baseline to judge staleness against
  else if (freshness === "stale") status = "offline"; // down (or silently not delivering) for 3s+
  else status = "reconnecting";

  return { status, lastTickAt, wsDeltaMs };
}

const STATUS_LABEL = { connecting: "CONNECTING", live: "LIVE", reconnecting: "RECONNECTING", offline: "OFFLINE" };

// Deliberately lean: only stats that are actually true right now (paper
// mode, a real clock, a REAL WebSocket connection state, real order-submit
// latency percentiles, and a real per-tick WS delivery delta) -- not
// decorative filler like a process-id number this codebase has no honest
// way to source. Latency/delta both render "-" (never 0, never a guess)
// before their first real sample -- same "don't fake a metric" discipline
// the rest of algoterminal's backend already follows (e.g. app/routers/
// risk.py introspects real values instead of hardcoding plausible ones).
export function StatusBar() {
  const now = useClock();
  const ist = now.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
  const mode = useMode();
  const { status, lastTickAt, wsDeltaMs } = useConnectionStatus(mode);
  const orderLatency = useOrderLatency();
  const density = useDensity();
  const theme = useTheme();
  const pendingChord = usePendingChord();

  return html`
    <div class="statusbar">
      <span class="statusbar-item">
        <span class=${`conn-dot ${status}`} />
        SYSTEM: ${STATUS_LABEL[status]}
      </span>
      <span class="statusbar-item">LAST UPDATE ${formatAge(lastTickAt, now.getTime())}</span>
      <span class="statusbar-item" title="Client-measured: Date.now() minus the tick's own server-stamped sent_at">
        WS Δ ${wsDeltaMs === null ? dash() : `${wsDeltaMs}ms`}
      </span>
      <span class="statusbar-item" title="Order-submit latency, p50 / p99 over recent submits">
        SUBMIT ${orderLatency ? `${orderLatency.p50_ms.toFixed(1)} / ${orderLatency.p99_ms.toFixed(1)}ms` : dash()}
      </span>
      <span class="statusbar-item">MODE: ${mode.toUpperCase()}</span>
      ${pendingChord && html`
        <span class="statusbar-item chord-hint">${pendingChord.map((k) => k.toUpperCase()).join(" ")}…</span>
      `}
      <span class="statusbar-spacer" />
      <span class="statusbar-item density-toggle" role="group" aria-label="Density">
        <button class=${density === "comfortable" ? "active" : ""} onClick=${() => setDensity("comfortable")}>Comfortable</button>
        <button class=${density === "compact" ? "active" : ""} onClick=${() => setDensity("compact")}>Compact</button>
      </span>
      <button class="statusbar-item" style=${{ background: "transparent", border: "none", color: "inherit", font: "inherit", cursor: "pointer", padding: 0 }}
              onClick=${toggleTheme} aria-label="Toggle theme" title="Toggle light / dark theme">
        ${theme === "dark" ? "☾ DARK" : "☀ LIGHT"}
      </button>
      <span class="statusbar-item">IST ${ist}</span>
    </div>
  `;
}
