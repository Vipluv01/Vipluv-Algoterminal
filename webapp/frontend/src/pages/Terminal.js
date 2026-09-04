import React from "react";
import { html } from "../html.js";
import { api, subscribeMarketForMode } from "../api.js";
import { fmtMoney, fmtNum } from "../format.js";
import { CandleChart } from "../components/CandleChart.js";
import { OrderBook } from "../components/OrderBook.js";
import { OrderEntry } from "../components/OrderEntry.js";
import { AccountPanel } from "../components/AccountPanel.js";
import { TimeAndSales } from "../components/TimeAndSales.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { LiveSymbolSearch } from "../components/LiveSymbolSearch.js";
import { SymbolSearch } from "../components/SymbolSearch.js";
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
          // lastPrice: a real fallback for the header's own price cell
          // for the gap between page load and the live WS delivering its
          // first tick -- this is the SAME real LTP already fetched here
          // to compute changePct, not a second, separate value.
          //
          // bids/asks: real order-book depth (getMarketData's own FULL-
          // mode depth.buy/depth.sell, see angelone.py's Quote docstring)
          // -- the live WS feed's own ticks carry NONE (Angel One's LTP-
          // mode stream, no depth at all), which is the real reason the
          // order book always read as empty in live mode. Reused from
          // this SAME already-scheduled quotes call rather than adding a
          // second poll -- one real Angel One request serving both the
          // header stats and the order book depth.
          setStats({
            changePct, high: bar?.high ?? null, low: bar?.low ?? null, volume: bar?.volume ?? null,
            lastPrice: q?.ltp ?? null, bids: q?.bids ?? [], asks: q?.asks ?? [],
          });
        } else {
          const hist = await api.market.history(symbol, "1hr", 24);
          if (cancelled) return;
          const bars = hist.bars;
          if (!bars.length) { setStats({ changePct: null, high: null, low: null, volume: null, lastPrice: null, bids: [], asks: [] }); return; }
          const high = Math.max(...bars.map((b) => b.high));
          const low = Math.min(...bars.map((b) => b.low));
          const volume = bars.reduce((s, b) => s + (b.volume ?? 0), 0);
          const changePct = bars[0].open ? ((bars[bars.length - 1].close - bars[0].open) / bars[0].open) * 100 : null;
          setStats({ changePct, high, low, volume, lastPrice: bars[bars.length - 1].close });
        }
      } catch {
        if (!cancelled) setStats({ changePct: null, high: null, low: null, volume: null, lastPrice: null, bids: [], asks: [] });
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

function SymbolStatsHeader({ symbol, price, mode, stats }) {
  const windowLabel = mode === "live" ? "24h" : "Session";
  const changeClass = stats?.changePct == null ? "" : stats.changePct >= 0 ? "pos" : "neg";
  // Prefer the live WS tick's own price; fall back to the real LTP/close
  // useSymbolStats already fetched (never a fabricated placeholder) for
  // the gap between page load and the WS delivering its first tick --
  // that gap otherwise showed a bare "—" even once real data existed.
  const displayPrice = price ?? stats?.lastPrice ?? null;
  return html`
    <div class="symbol-stats-header">
      <div>
        <div class="stat-label">${symbol}</div>
        <div class="mono" style=${{ fontSize: "20px", fontWeight: 700 }}>${displayPrice ? fmtMoney(displayPrice) : "—"}</div>
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

const RECENT_SYMBOLS_LIMIT = 8;

// Per-mode recency, in localStorage -- paper/virtual/live each have their
// own tradable universe (see Terminal()'s own visibleSymbols comment), so
// a symbol recently viewed in one mode isn't necessarily even valid in
// another; keying by mode keeps the three lists from bleeding into each
// other, the same isolation the search bar itself already has (a
// different `names` list per mode). Persisted (not just component state)
// so "recently viewed" actually survives a page reload, which is the
// point of it as a quick-access shortcut.
function useRecentSymbols(mode, active) {
  const storageKey = `terminal_recent_symbols_${mode}`;
  const readStored = () => {
    try { return JSON.parse(localStorage.getItem(storageKey)) || []; } catch { return []; }
  };
  const [recent, setRecent] = React.useState(readStored);

  React.useEffect(() => {
    setRecent(readStored());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  React.useEffect(() => {
    if (!active) return;
    setRecent((prev) => {
      const next = [active, ...prev.filter((s) => s !== active)].slice(0, RECENT_SYMBOLS_LIMIT);
      try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* storage unavailable -- recency just won't persist */ }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, mode]);

  return recent;
}

function RecentSymbols({ recent, active, ticks, onSelect }) {
  if (!recent.length) return null;
  return html`
    <div class="recent-symbols">
      <span class="recent-symbols-label">Recently viewed</span>
      ${recent.map((sym) => {
        const t = ticks[sym];
        return html`
          <div key=${sym} class=${`chip ${active === sym ? "active" : ""}`} onClick=${() => onSelect(sym)}>
            <span style=${{ fontWeight: 700 }}>${sym}</span>
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
  //
  // Skipped entirely WHILE in live mode: `active` there can be a symbol
  // picked via LiveSymbolSearch below, which draws from the real ~2000+
  // equity universe (GET /live/market/equities), not the curated 7-name
  // `symbols` array this effect checks against -- a name simply not
  // being one of those 7 doesn't mean it's invalid, and blindly
  // rerouting away from it would undo every free-form search selection
  // on the very next render. It's already guaranteed live-tradable by
  // construction (the search list only ever contains real, currently-
  // listed equities). The real risk this effect guards against --
  // leaving `active` pointed at a symbol the CURRENT mode can't actually
  // trade -- only applies when landing on/staying in paper or virtual,
  // whose simulated engine only ever knows the fixed 7.
  React.useEffect(() => {
    if (tradingMode === "live") return;
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
  const stats = useSymbolStats(active, tradingMode);
  const recentSymbols = useRecentSymbols(tradingMode, active);
  // Live mode's WS tick carries NO depth (Angel One's LTP-mode stream,
  // confirmed -- see live_market.py's own _live_tick_to_payload). Merge
  // in the REAL depth useSymbolStats already polled via getMarketData's
  // FULL mode (the same real data the options chain uses) rather than
  // showing OrderBook an always-empty book in live mode. Paper/virtual
  // are untouched -- their own WS tick already carries real simulated
  // depth directly, nothing to merge.
  const orderBookTick = tradingMode === "live" && tick
    ? {
        ...tick, bids: stats?.bids ?? [], asks: stats?.asks ?? [],
        // OrderBook's own Depth-tab mid-price/spread read best_bid/
        // best_ask directly (not derived from bids/asks itself) -- the
        // live WS tick's own values are always null (see above), so
        // these need the same real-depth override, taken from the SAME
        // real bids/asks just merged in (their own best/first level).
        best_bid: stats?.bids?.[0]?.px ?? null, best_ask: stats?.asks?.[0]?.px ?? null,
      }
    : tick;
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

      ${tradingMode === "live"
        ? html`<${LiveSymbolSearch} onSelect=${setActive} />`
        : html`<${SymbolSearch} names=${visibleSymbols.map((s) => s.symbol)} onSelect=${setActive}
                                placeholder=${`Search ${visibleSymbols.length} tradable stocks…`} />`}

      <${RecentSymbols} recent=${recentSymbols} active=${active} ticks=${ticks} onSelect=${setActive} />

      ${active && html`
        <${ErrorBoundary} label="Symbol Stats">
          <${SymbolStatsHeader} symbol=${active} price=${tick?.price} mode=${tradingMode} stats=${stats} />
        <//>
      `}

      ${active && html`
        <div class="terminal-grid">
          <div style=${{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <${ErrorBoundary} label="Order Book">
              <${OrderBook} tick=${orderBookTick} stale=${status !== "live"}
                            onLevelClick=${(side, price) => setOrderPrefill({ side, price, nonce: Date.now() })} />
            <//>
            <${ErrorBoundary} label="Time & Sales"><${TimeAndSales} tick=${tick} symbol=${active} /><//>
          </div>
          <div class="panel panel-pad">
            <${ErrorBoundary} label="Chart">
              <${CandleChart} symbol=${active} price=${tick?.price} stale=${status !== "live"} />
            <//>
          </div>
          <${ErrorBoundary} label="Order Entry">
            <${OrderEntry} symbol=${active} price=${tick?.price} prefill=${orderPrefill}
                           onOrderPlaced=${() => setRefreshKey((k) => k + 1)} />
          <//>
        </div>
        <div style=${{ marginTop: "14px" }}>
          <${ErrorBoundary} label="Account Panel"><${AccountPanel} refreshKey=${refreshKey} /><//>
        </div>
      `}
    </div>
  `;
}
