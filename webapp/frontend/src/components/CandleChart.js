import React from "react";
import { init, dispose, ActionType } from "klinecharts";
import { html } from "../html.js";
import { Modal } from "./Modal.js";
import { SkeletonBlock } from "./Skeleton.js";
import { api } from "../api.js";
import { useMode } from "../mode.js";

// 1s/5s were dropped in favor of the two longer timeframes the product
// actually needs (30m/1hr) -- both of which are USELESS without the
// history-seeding fetch below, since at one tick/second a 1hr candle would
// otherwise take a full hour of live watching to draw its first bar. Keys
// match GET /market/history's own `interval` enum (app/routers/market.py)
// exactly, since a pane's interval key is sent there verbatim.
const SIM_CANDLE_SECONDS_OPTIONS = { "1m": 60, "5m": 300, "30m": 1800, "1hr": 3600 };
// Live mode's options are a DIFFERENT set, not a subset -- Angel One's
// candle API only offers whole-minute-and-up granularity with its own
// specific steps (1m/15m/1hr/1d), not 5m/30m, and does have a daily bar
// paper/virtual have no equivalent of (see app/routers/live_market.py's
// own module docstring). Matches GET /live/market/history's `interval`
// enum exactly, for the same reason as the sim map above.
const LIVE_CANDLE_SECONDS_OPTIONS = { "1m": 60, "15m": 900, "1hr": 3600, "1d": 86400 };

function optionsForMode(mode) {
  return mode === "live" ? LIVE_CANDLE_SECONDS_OPTIONS : SIM_CANDLE_SECONDS_OPTIONS;
}

const DEFAULT_CANDLE_SECONDS = SIM_CANDLE_SECONDS_OPTIONS["1m"]; // == LIVE's own "1m" too -- both maps agree on this one key

const HISTORY_BARS = 300;

function secondsToKey(secs, mode) {
  return Object.entries(optionsForMode(mode)).find(([, v]) => v === secs)?.[0];
}

const MAIN_INDICATORS = ["MA", "EMA", "BOLL", "SAR", "BBI"];
const SUB_INDICATORS = ["VOL", "MACD", "RSI", "KDJ"];

// klinecharts' own documented styles.candle.type values -- a pure view
// toggle over the SAME real OHLC series (see the setStyles effect
// below), never a second data source. "Line" maps to klinecharts' own
// 'ohlc' style (the closest non-filled encoding it offers) rather than a
// bare "line" type this library doesn't distinctly expose.
const CHART_TYPES = [
  { key: "candle_solid", label: "Candles" },
  { key: "ohlc", label: "Line" },
  { key: "area", label: "Area" },
];

function fmtOhlc(v) {
  return v == null ? "—" : v.toFixed(2);
}

// Aggregates a stream of {price, timestamp} ticks into OHLCV candles,
// bucketed by wall-clock time -- klinecharts wants real timestamps, not
// a tick index, since its x-axis renders actual times. Seeded from GET
// /market/history on mount and on every symbol/interval change, THEN
// handed off to live ticks -- the seed and the live aggregator bucket
// timestamps with the exact same formula (bucket = floor(ts_ms /
// interval_ms), see market.py's _aggregate_bars docstring) specifically so
// a live tick right after the seed extends the seed's own last bar instead
// of gapping, duplicating, or misaligning with it.
function useCandleAggregator(symbol, candleSeconds, mode) {
  const [loading, setLoading] = React.useState(true);
  // The current (or, briefly after a seed with nothing forming yet, the
  // last finalized) candle's own real OHLC -- surfaced for the toolbar's
  // legend badges, NOT re-derived or guessed separately; same object
  // every tick already updates the chart itself with.
  const [latestCandle, setLatestCandle] = React.useState(null);
  const candlesRef = React.useRef([]);      // finalized candles
  const currentRef = React.useRef(null);     // in-progress candle
  const bucketStartRef = React.useRef(0);    // bucket NUMBER (not ms) of currentRef/the last-seen bucket
  const chartApiRef = React.useRef(null);    // set by the chart once mounted

  const applyToChart = React.useCallback(() => {
    const chart = chartApiRef.current;
    if (!chart) return;
    const all = currentRef.current ? [...candlesRef.current, currentRef.current] : candlesRef.current;
    chart.applyNewData(all);
    setLatestCandle(all.length ? all[all.length - 1] : null);
  }, []);

  const onTick = React.useCallback((price) => {
    const now = Date.now();
    const bucket = Math.floor(now / (candleSeconds * 1000));

    if (bucketStartRef.current !== bucket) {
      // New bucket: finalize the previous candle (if any), start a new one.
      if (currentRef.current) candlesRef.current.push(currentRef.current);
      bucketStartRef.current = bucket;
      currentRef.current = { timestamp: bucket * candleSeconds * 1000, open: price, high: price, low: price, close: price, volume: 1 };
      if (candlesRef.current.length > 300) candlesRef.current.shift();
    } else if (currentRef.current) {
      currentRef.current.high = Math.max(currentRef.current.high, price);
      currentRef.current.low = Math.min(currentRef.current.low, price);
      currentRef.current.close = price;
      currentRef.current.volume += 1;
    } else {
      currentRef.current = { timestamp: bucket * candleSeconds * 1000, open: price, high: price, low: price, close: price, volume: 1 };
    }

    if (chartApiRef.current) {
      chartApiRef.current.updateData({ ...currentRef.current });
    }
    setLatestCandle({ ...currentRef.current });
  }, [candleSeconds]);

  const seedChart = React.useCallback((chart) => {
    chartApiRef.current = chart;
    applyToChart();
  }, [applyToChart]);

  // Reset local aggregation AND re-seed from real history whenever the
  // symbol, the candle bucket size, OR the trading mode changes -- a 1m
  // candle series built out of leftover 5m-bucketed local state (or a new
  // instrument's ticks, or the OTHER data source's bars) would just be
  // wrong, not merely coarser. Mode picks which history endpoint seeds
  // the chart (simulated engine vs real Angel One candles) -- the live
  // WS subscription itself is a separate concern, owned by whichever page
  // calls subscribeMarketForMode, not this hook.
  React.useEffect(() => {
    let cancelled = false;
    candlesRef.current = [];
    currentRef.current = null;
    bucketStartRef.current = 0;
    setLoading(true);

    const intervalKey = secondsToKey(candleSeconds, mode);
    const fetchHistory = mode === "live" ? api.live.history : api.market.history;
    fetchHistory(symbol, intervalKey, HISTORY_BARS)
      .then((hist) => {
        if (cancelled) return;
        const bars = hist.bars.map((b) => ({
          timestamp: b.timestamp, open: b.open, high: b.high, low: b.low, close: b.close,
          // A bar older than the retained volume window genuinely has no
          // real traded quantity on file (see BarOut.volume's own
          // docstring) -- 0 here is a chart-rendering fallback for "not
          // retained", not a claim that nothing traded; there's no honest
          // non-zero number to put in its place, and leaving this
          // undefined breaks klinecharts' VOL pane.
          volume: b.volume ?? 0,
        }));
        const nowBucket = Math.floor(Date.now() / (candleSeconds * 1000));
        const lastBucket = bars.length ? Math.floor(bars[bars.length - 1].timestamp / (candleSeconds * 1000)) : null;
        if (lastBucket === nowBucket) {
          // The newest historical bar IS the current, still-forming bucket
          // -- treat it as the in-progress candle so the next live tick
          // EXTENDS it instead of creating a duplicate at the same
          // timestamp.
          currentRef.current = bars[bars.length - 1];
          candlesRef.current = bars.slice(0, -1);
          bucketStartRef.current = nowBucket;
        } else {
          // Nothing has traded in the current bucket yet -- every returned
          // bar is finalized, and bucketStartRef is set to the last real
          // bucket seen (not `nowBucket`) so the next live tick correctly
          // reads as a NEW bucket rather than silently reopening a bar
          // that was already closed.
          candlesRef.current = bars;
          currentRef.current = null;
          bucketStartRef.current = lastBucket ?? 0;
        }
        applyToChart();
      })
      .catch(() => {
        // Seed failed -- fall back to an honestly empty chart (refs are
        // already reset above) rather than leaving the PREVIOUS symbol's/
        // interval's stale bars on screen once the loading skeleton lifts.
        applyToChart();
      })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, candleSeconds, mode]);

  return { onTick, seedChart, loading, latestCandle };
}

// The first consumer of the generic Modal primitive -- was its own bespoke
// backdrop+panel (no focus trap, no Escape handling, no restore-focus-on-
// close) before Modal.js existed to provide all three for free.
function IndicatorPicker({ active, onToggle, onClose }) {
  return html`
    <${Modal} title="Indicators" onClose=${onClose} size="sm">
      <div class="indicator-picker-section" style=${{ marginTop: 0 }}>Main (overlay)</div>
      ${MAIN_INDICATORS.map((name) => html`
        <label key=${name} class="indicator-picker-row">
          <input type="checkbox" checked=${active.has(name)} onChange=${() => onToggle(name, "candle_pane")} />
          ${name}
        </label>
      `)}
      <div class="indicator-picker-section">Sub (own pane)</div>
      ${SUB_INDICATORS.map((name) => html`
        <label key=${name} class="indicator-picker-row">
          <input type="checkbox" checked=${active.has(name)} onChange=${() => onToggle(name, null)} />
          ${name}
        </label>
      `)}
    <//>
  `;
}

export function CandleChart({ symbol, price, height = "440px", stale = false, onCrosshairMove, syncCrosshair, initialIntervalKey, onIntervalChange }) {
  const containerId = React.useId().replace(/:/g, "-");
  const wrapRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const paneIdByIndicatorRef = React.useRef({}); // name -> paneId, needed to removeIndicator later
  // Set right before this pane programmatically moves its OWN crosshair to
  // match a sibling's (via executeAction below) and cleared right after --
  // without it, that programmatic move would fire THIS pane's own
  // subscribeAction callback, which would call onCrosshairMove, which
  // would tell every OTHER pane to move, including the original sender:
  // an infinite ping-pong across every synced pane.
  const applyingExternalSyncRef = React.useRef(false);
  const mode = useMode();
  const candleOptions = optionsForMode(mode);
  // initialIntervalKey can be stale (a value persisted to localStorage
  // before 1s/5s were dropped, e.g. Charts.js's PANES_KEY) -- an unknown
  // key falls back to DEFAULT_CANDLE_SECONDS rather than producing
  // `undefined` and silently breaking the aggregator's bucket math.
  const [candleSeconds, setCandleSecondsRaw] = React.useState(candleOptions[initialIntervalKey] || DEFAULT_CANDLE_SECONDS);
  function setCandleSeconds(secs) {
    setCandleSecondsRaw(secs);
    if (onIntervalChange) {
      const key = secondsToKey(secs, mode);
      if (key) onIntervalChange(key);
    }
  }
  // The two interval sets aren't a subset of each other (live has 15m/1d,
  // sim has 5m/30m) -- switching mode while sitting on a value the OTHER
  // set doesn't have (e.g. 30m, then flipping to live) must snap to a
  // valid one instead of silently asking the new data source for an
  // interval it doesn't support.
  React.useEffect(() => {
    if (!Object.values(candleOptions).includes(candleSeconds)) {
      setCandleSecondsRaw(DEFAULT_CANDLE_SECONDS);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);
  const [active, setActive] = React.useState(() => new Set(["MA", "VOL"]));
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [isFullscreen, setIsFullscreen] = React.useState(false);
  // klinecharts' own candle.type styles -- a view toggle, not a data
  // change: every value here renders the SAME real OHLC series this
  // component already aggregates, just with a different visual encoding.
  const [chartType, setChartType] = React.useState("candle_solid");
  const { onTick, seedChart, loading, latestCandle } = useCandleAggregator(symbol, candleSeconds, mode);

  React.useEffect(() => {
    const chart = init(containerId, {
      styles: {
        grid: { horizontal: { color: "#1f293b" }, vertical: { color: "#1f293b" } },
        candle: {
          // #00e676/#ff1744 -- the SAME --bid/--ask hex values the rest
          // of the app moved to earlier this session (theme.css); these
          // were hardcoded here and missed that update, so the chart's
          // own up/down colors had quietly drifted from every other
          // bid/ask-colored element (OrderBook's depth bars, the
          // ticker's pos/neg text) back to the old green/red pair.
          bar: { upColor: "#00e676", downColor: "#ff1744", upBorderColor: "#00e676", downBorderColor: "#ff1744", upWickColor: "#00e676", downWickColor: "#ff1744" },
          priceMark: { last: { upColor: "#00e676", downColor: "#ff1744" } },
          tooltip: { textColor: "#8892a6" },
        },
        xAxis: { axisLine: { color: "#1f293b" }, tickText: { color: "#5a6478" } },
        yAxis: { axisLine: { color: "#1f293b" }, tickText: { color: "#5a6478" } },
        crosshair: { horizontal: { line: { color: "#2dd4bf" } }, vertical: { line: { color: "#2dd4bf" } } },
      },
    });
    paneIdByIndicatorRef.current.MA = chart.createIndicator("MA", false, { id: "candle_pane" });
    paneIdByIndicatorRef.current.VOL = chart.createIndicator("VOL", false, { id: "volume_pane" });
    chartRef.current = chart;
    seedChart(chart);

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);

    if (onCrosshairMove) {
      chart.subscribeAction(ActionType.OnCrosshairChange, (data) => {
        if (applyingExternalSyncRef.current) return; // see the ref's own comment above
        if (!data || data.dataIndex === undefined) return;
        onCrosshairMove(data.dataIndex);
      });
    }

    return () => {
      window.removeEventListener("resize", onResize);
      if (onCrosshairMove) chart.unsubscribeAction(ActionType.OnCrosshairChange);
      dispose(containerId);
      chartRef.current = null;
      paneIdByIndicatorRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  // View toggle only -- restyles how the SAME already-aggregated candles
  // render (klinecharts' own candle.type), never touches candlesRef/
  // currentRef or re-fetches anything. try/catch is defensive only: a
  // future klinecharts upgrade rejecting an unexpected type string
  // should degrade to "toggle did nothing this render" instead of
  // crashing the whole chart.
  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    try {
      chart.setStyles({ candle: { type: chartType } });
    } catch {
      /* non-fatal -- see comment above */
    }
  }, [chartType, symbol]);

  // Applies an incoming synced crosshair position (a data index broadcast
  // by a SIBLING pane) to this chart -- see Charts.js for the broadcast
  // side. null means "no crosshair anywhere right now" (e.g. the mouse
  // left every pane), which clears this pane's crosshair too.
  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart || syncCrosshair === undefined) return;
    applyingExternalSyncRef.current = true;
    try {
      chart.executeAction(ActionType.OnCrosshairChange, syncCrosshair === null ? {} : { dataIndex: syncCrosshair });
    } finally {
      applyingExternalSyncRef.current = false;
    }
  }, [syncCrosshair]);

  React.useEffect(() => {
    if (price !== null && price !== undefined) onTick(price);
  }, [price, onTick]);

  React.useEffect(() => {
    const onFsChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  function toggleIndicator(name, overlayPaneId) {
    const chart = chartRef.current;
    if (!chart) return;
    setActive((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        const paneId = paneIdByIndicatorRef.current[name];
        chart.removeIndicator(overlayPaneId || paneId, name);
        delete paneIdByIndicatorRef.current[name];
        next.delete(name);
      } else {
        const paneId = chart.createIndicator(name, false, overlayPaneId ? { id: overlayPaneId } : undefined);
        paneIdByIndicatorRef.current[name] = paneId;
        next.add(name);
      }
      return next;
    });
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else if (wrapRef.current) {
      wrapRef.current.requestFullscreen();
    }
  }

  return html`
    <div ref=${wrapRef} class="candle-wrap">
      <div class="candle-toolbar">
        <div class="toggle-row candle-timeframes">
          ${Object.entries(candleOptions).map(([label, secs]) => html`
            <button key=${label} class=${`btn btn-sm ${candleSeconds === secs ? "active neon" : ""}`}
                    onClick=${() => setCandleSeconds(secs)}>${label}</button>
          `)}
        </div>
        <div class="toggle-row">
          ${CHART_TYPES.map((t) => html`
            <button key=${t.key} class=${`btn btn-sm ${chartType === t.key ? "active neon" : ""}`}
                    onClick=${() => setChartType(t.key)}>${t.label}</button>
          `)}
        </div>
        <div style=${{ flex: 1 }} />
        <button class="btn btn-sm btn-ghost" onClick=${() => setPickerOpen(true)}>Indicators</button>
        <button class="btn btn-sm btn-ghost" onClick=${toggleFullscreen}>${isFullscreen ? "Exit Full Screen" : "Full Screen"}</button>
      </div>
      <div style=${{ position: "relative" }}>
        ${latestCandle && html`
          <div class="candle-ohlc-legend">
            <span>O <b class="mono">${fmtOhlc(latestCandle.open)}</b></span>
            <span>H <b class="mono pos">${fmtOhlc(latestCandle.high)}</b></span>
            <span>L <b class="mono neg">${fmtOhlc(latestCandle.low)}</b></span>
            <span>C <b class="mono">${fmtOhlc(latestCandle.close)}</b></span>
          </div>
        `}
        <div id=${containerId} class=${stale ? "is-stale" : ""}
             style=${{ width: "100%", height: isFullscreen ? "calc(100vh - 40px)" : height }} />
        ${loading && html`
          <${SkeletonBlock} height=${isFullscreen ? "calc(100vh - 40px)" : height}
                             style=${{ position: "absolute", inset: 0 }} />
        `}
      </div>
      ${pickerOpen && html`<${IndicatorPicker} active=${active} onToggle=${toggleIndicator} onClose=${() => setPickerOpen(false)} />`}
    </div>
  `;
}
