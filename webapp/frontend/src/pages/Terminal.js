import React from "react";
import { html } from "../html.js";
import { api, subscribeMarketForMode } from "../api.js";
import { fmtMoney, fmtNum } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";
import { OrderBook } from "../components/OrderBook.js";
import { OrderEntry } from "../components/OrderEntry.js";
import { AccountPanel } from "../components/AccountPanel.js";
import { TimeAndSales } from "../components/TimeAndSales.js";
import { DEFAULT_STALE_THRESHOLD_MS, useStaleness } from "../clock.js";
import { useMode } from "../mode.js";

const STATUS_LABEL = { connecting: "Connecting…", live: "Live", reconnecting: "Reconnecting…", offline: "Offline", disconnected: "Disconnected — not retrying" };

// Real change%/high/low/volume, never a placeholder sitting next to a
// real price. Live mode uses api.live.quotes (real LTP vs real previous
// close -- the same fix Ticker.js's own %-change bug used) plus one real
// daily bar (Angel One aggregates that day's own high/low/volume server
// side, more reliable than re-aggregating 1m bars here and risking a
// multi-day window if the intraday lookback happens to span one -- see
// live_market.py's own 10-day-floor lookback comment on why that can
// happen). Paper/virtual has no "1d" interval (the simulated engine's
// price_history is per-second/per-minute, not calendar-day-aware) and no
// real previous-close concept either, so it's labeled "Session" and
// derived from the last 24 real 1hr bars instead -- an honestly
// different (and honestly labeled) window, not a forced "24h" claim
// paper mode has no basis for.
function useSymbolStats(symbol, mode) {
  const [stats, setStats] = React.useState(null);

  React.useEffect(() => {
    if (!symbol) { setStats(null); return; }
    let cancelled = false;

    async function load() {
      try {
        if (mode === "live") {
          const [quotesRes, histRes] = await Promise.all([
            api.live.quotes([symbol]),
            api.live.history(symbol, "1d", 1),
          ]);
          if (cancelled) return;
          const q = quotesRes.quotes[0];
          const bar = histRes.bars[histRes.bars.length - 1];
          const changePct = q?.ltp != null && q?.close ? ((q.ltp - q.close) / q.close) * 100 : null;
          setStats({ changePct, high: bar?.high ?? null, low: bar?.low ?? null, volume: bar?.volume ?? null });
        } else {
          const hist = await api.market.history(symbol, "1hr", 24);
          if (cancelled) return;
          const bars = hist.bars;
          if (!bars.length) { setStats({ changePct: null, high: null, low: null, volume: null }); return; }
          const high = Math.max(...bars.map((b) => b.high));
          const low = Math.min(...bars.map((b) => b.low));
          const volume = bars.reduce((s, b) => s + (b.volume ?? 0), 0);
          const changePct = bars[0].open ? ((bars[bars.length - 1].close - bars[0].open) / bars[0].open) * 100 : null;
          setStats({ changePct, high, low, volume });
        }
      } catch {
        if (!cancelled) setStats({ changePct: null, high: null, low: null, volume: null });
      }
    }
    load();
    // Live polls slower -- this is a real Angel One call (quotes +
    // history), not free the way the simulated engine's own local
    // aggregate is; no reason to hit it as often as paper's.
    const id = setInterval(load, mode === "live" ? 30000 : 10000);
    return () => { cancelled = true; clearInterval(id); };
  }, [symbol, mode]);

  return stats;
}

function SymbolStatsHeader({ symbol, price, mode }) {
  const stats = useSymbolStats(symbol, mode);
  const windowLabel = mode === "live" ? "24h" : "Session";
  const changeClass = stats?.changePct == null ? "" : stats.changePct >= 0 ? "pos" : "neg";
  return html`
    <div class="symbol-stats-header">
      <div>
        <div class="stat-label">${symbol}</div>
        <div class="mono" style=${{ fontSize: "20px", fontWeight: 700 }}>${price ? fmtMoney(price) : "—"}</div>
      </div>
      <div>
        <div class="stat-label">${windowLabel} Change</div>
        <div class=${`mono stat-value ${changeClass}`}>
          ${stats?.changePct == null ? "—" : `${stats.changePct >= 0 ? "+" : ""}${stats.changePct.toFixed(2)}%`}
        </div>
      </div>
      <div>
        <div class="stat-label">${windowLabel} High</div>
        <div class="mono stat-value">${stats?.high != null ? fmtMoney(stats.high) : "—"}</div>
      </div>
      <div>
        <div class="stat-label">${windowLabel} Low</div>
        <div class="mono stat-value">${stats?.low != null ? fmtMoney(stats.low) : "—"}</div>
      </div>
      <div>
        <div class="stat-label">${windowLabel} Volume</div>
        <div class="mono stat-value">${stats?.volume != null ? fmtNum(stats.volume) : "—"}</div>
      </div>
    </div>
  `;
}

function SymbolTabs({ symbols, active, onSelect, ticks }) {
  return html`
    <div style=${{ display: "flex", gap: "8px", overflowX: "auto", paddingBottom: "4px", marginBottom: "16px" }}>
      ${symbols.map((s) => {
        const t = ticks[s.symbol];
        return html`
          <div key=${s.symbol} class=${`chip ${active === s.symbol ? "active" : ""}`} onClick=${() => onSelect(s.symbol)}>
            <span style=${{ fontWeight: 700 }}>${s.symbol}</span>
            ${t?.price && html`<span class="mono" style=${{ marginLeft: "8px", color: "var(--text-dim)" }}>${fmtMoney(t.price)}</span>`}
          </div>
        `;
      })}
    </div>
  `;
}

export function Terminal() {
  const [symbols, setSymbols] = React.useState([]);
  const [active, setActive] = React.useState(null);
  const [ticks, setTicks] = React.useState({});
  const [wsStatus, setWsStatus] = React.useState("connecting");
  const [lastTickAt, setLastTickAt] = React.useState(null);
  const [refreshKey, setRefreshKey] = React.useState(0);
  // {side, price, nonce} | null -- see OrderEntry.js's prefill prop
  // comment for why `nonce` matters (re-clicking a level with the same
  // side needs to re-apply, not be a no-op React skips as "unchanged").
  const [orderPrefill, setOrderPrefill] = React.useState(null);
  const tradingMode = useMode();

  React.useEffect(() => {
    api.symbols().then((rows) => {
      setSymbols(rows);
      if (rows.length) setActive(rows[0].symbol);
    });
  }, []);

  // live_tradable (app/main.py's /symbols, backed by Angel One's own
  // real instrument master) -- a symbol this app has always simulated
  // fine in paper/virtual mode is not guaranteed to be one the REAL
  // broker currently lists (confirmed live, 2026-09-03: TATAMOTORS has
  // zero real listings, almost certainly the real corporate demerger --
  // a user hit exactly this trying to place a live order on it). Only
  // filters in live mode -- paper/virtual never cared and still don't.
  const visibleSymbols = tradingMode === "live" ? symbols.filter((s) => s.live_tradable) : symbols;

  // If the currently active symbol isn't tradable in the mode just
  // switched to (e.g. sitting on TATAMOTORS in paper, then flipping to
  // live), jump to the first symbol that IS -- an order form left
  // pointed at an unavailable symbol is exactly the failure this whole
  // filter exists to prevent, not just hiding it from the tab row.
  React.useEffect(() => {
    if (!active || !visibleSymbols.length) return;
    if (!visibleSymbols.some((s) => s.symbol === active)) {
      setActive(visibleSymbols[0].symbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tradingMode, symbols]);

  React.useEffect(() => {
    if (!active) return;
    setWsStatus("connecting");
    setLastTickAt(null);
    const unsub = subscribeMarketForMode(
      tradingMode,
      active,
      (tick) => {
        setLastTickAt(Date.now());
        setTicks((prev) => ({ ...prev, [active]: tick }));
      },
      setWsStatus,
    );
    return unsub;
  }, [active, tradingMode]);

  // Same derivation StatusBar.js uses: raw WS status alone can't tell a
  // connection that dropped a moment ago from one that's been down for a
  // minute (both report "reconnecting"), and tick arrival alone is a
  // one-way ratchet -- the PREVIOUS version of this badge set `connected`
  // to true on the first tick and never back to false, so it kept
  // claiming "Live" through a killed backend (found via Gap 5's actual
  // kill-the-backend test, tests/e2e/gap5_staleness.py). Elapsed time
  // since the last real tick (useStaleness) is what actually tells them
  // apart.
  const freshness = useStaleness(lastTickAt, DEFAULT_STALE_THRESHOLD_MS);
  let status;
  // "disconnected" (api.js's live-feed circuit breaker gave up) checked
  // first, ahead of staleness -- see StatusBar.js's identical guard for
  // why: it's a genuine stopped state, not "still trying, just stale".
  if (wsStatus === "disconnected") status = "disconnected";
  else if (wsStatus === "live" && freshness === "fresh") status = "live";
  else if (lastTickAt === null) status = wsStatus;
  else if (freshness === "stale") status = "offline";
  else status = "reconnecting";

  const tick = ticks[active];
  const modeSubtitle = tradingMode === "live"
    ? "Live trading via Angel One — every order is human-confirmed before it reaches the broker"
    : tradingMode === "virtual"
      ? "Virtual trading (₹1 Cr simulated capital) against the real bourse matching engine"
      : "Paper trading against the real bourse matching engine";
  // A real, computed staleness string for the status pill's tooltip, not
  // just the coarse live/reconnecting/offline label -- lastTickAt is the
  // actual last real tick this connection received.
  const staleTooltip = lastTickAt === null
    ? "No tick received yet"
    : `Last tick ${Math.max(0, Math.round((Date.now() - lastTickAt) / 1000))}s ago`;

  return html`
    <div class="page fade-in">
      <div style=${{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <div>
          <h1 style=${{ margin: 0, fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Terminal</h1>
          <div style=${{ color: "var(--text-faint)", fontSize: "12px", marginTop: "2px" }}>${modeSubtitle}</div>
        </div>
        <div class="status-pill" title=${staleTooltip}>
          <span class=${`status-dot ${status === "live" ? "live" : "dead"}`} />
          ${STATUS_LABEL[status]}
        </div>
      </div>

      <${SymbolTabs} symbols=${visibleSymbols} active=${active} onSelect=${setActive} ticks=${ticks} />

      ${active && html`<${SymbolStatsHeader} symbol=${active} price=${tick?.price} mode=${tradingMode} />`}

      ${active && html`
        <div class="terminal-grid">
          <div style=${{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <${OrderBook} tick=${tick} stale=${status !== "live"}
                          onLevelClick=${(side, price) => setOrderPrefill({ side, price, nonce: Date.now() })} />
            <${TimeAndSales} tick=${tick} symbol=${active} />
          </div>
          <div class="panel panel-pad">
            <${CandleChart} symbol=${active} price=${tick?.price} stale=${status !== "live"} />
          </div>
          <${OrderEntry} symbol=${active} price=${tick?.price} prefill=${orderPrefill}
                         onOrderPlaced=${() => setRefreshKey((k) => k + 1)} />
        </div>
        <div style=${{ marginTop: "14px" }}>
          <${AccountPanel} refreshKey=${refreshKey} />
        </div>
      `}
    </div>
  `;
}
