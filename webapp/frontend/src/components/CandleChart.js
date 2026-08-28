import React from "react";
import { init, dispose, ActionType } from "klinecharts";
import { html } from "../html.js";
import { Modal } from "./Modal.js";
import { SkeletonBlock } from "./Skeleton.js";
import { api } from "../api.js";

// 1s/5s were dropped in favor of the two longer timeframes the product
// actually needs (30m/1hr) -- both of which are USELESS without the
// history-seeding fetch below, since at one tick/second a 1hr candle would
// otherwise take a full hour of live watching to draw its first bar. Keys
// match GET /market/history's own `interval` enum (app/routers/market.py)
// exactly, since a pane's interval key is sent there verbatim.
const CANDLE_SECONDS_OPTIONS = { "1m": 60, "5m": 300, "30m": 1800, "1hr": 3600 };
const DEFAULT_CANDLE_SECONDS = CANDLE_SECONDS_OPTIONS["1m"];
const HISTORY_BARS = 300;

function secondsToKey(secs) {
  return Object.entries(CANDLE_SECONDS_OPTIONS).find(([, v]) => v === secs)?.[0];
}

const MAIN_INDICATORS = ["MA", "EMA", "BOLL", "SAR", "BBI"];
const SUB_INDICATORS = ["VOL", "MACD", "RSI", "KDJ"];

// Aggregates a stream of {price, timestamp} ticks into OHLCV candles,
// bucketed by wall-clock time -- klinecharts wants real timestamps, not
// a tick index, since its x-axis renders actual times. Seeded from GET
// /market/history on mount and on every symbol/interval change, THEN
// handed off to live ticks -- the seed and the live aggregator bucket
// timestamps with the exact same formula (bucket = floor(ts_ms /
// interval_ms), see market.py's _aggregate_bars docstring) specifically so
// a live tick right after the seed extends the seed's own last bar instead
// of gapping, duplicating, or misaligning with it.
function useCandleAggregator(symbol, candleSeconds) {
  const [loading, setLoading] = React.useState(true);
  const candlesRef = React.useRef([]);      // finalized candles
  const currentRef = React.useRef(null);     // in-progress candle
  const bucketStartRef = React.useRef(0);    // bucket NUMBER (not ms) of currentRef/the last-seen bucket
  const chartApiRef = React.useRef(null);    // set by the chart once mounted

  const applyToChart = React.useCallback(() => {
    const chart = chartApiRef.current;
    if (!chart) return;
    const all = currentRef.current ? [...candlesRef.current, currentRef.current] : candlesRef.current;
    chart.applyNewData(all);
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
  }, [candleSeconds]);

  const seedChart = React.useCallback((chart) => {
    chartApiRef.current = chart;
    applyToChart();
  }, [applyToChart]);

  // Reset local aggregation AND re-seed from real history whenever the
  // symbol OR the candle bucket size changes -- a 1m candle series built
  // out of leftover 5m-bucketed local state (or a new instrument's ticks)
  // would just be wrong, not merely coarser.
  React.useEffect(() => {
    let cancelled = false;
    candlesRef.current = [];
    currentRef.current = null;
    bucketStartRef.current = 0;
    setLoading(true);

    const intervalKey = secondsToKey(candleSeconds);
    api.market.history(symbol, intervalKey, HISTORY_BARS)
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
  }, [symbol, candleSeconds]);

  return { onTick, seedChart, loading };
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
  // initialIntervalKey can be stale (a value persisted to localStorage
  // before 1s/5s were dropped, e.g. Charts.js's PANES_KEY) -- an unknown
  // key falls back to DEFAULT_CANDLE_SECONDS rather than producing
  // `undefined` and silently breaking the aggregator's bucket math.
  const [candleSeconds, setCandleSecondsRaw] = React.useState(CANDLE_SECONDS_OPTIONS[initialIntervalKey] || DEFAULT_CANDLE_SECONDS);
  function setCandleSeconds(secs) {
    setCandleSecondsRaw(secs);
    if (onIntervalChange) {
      const key = secondsToKey(secs);
      if (key) onIntervalChange(key);
    }
  }
  const [active, setActive] = React.useState(() => new Set(["MA", "VOL"]));
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [isFullscreen, setIsFullscreen] = React.useState(false);
  const { onTick, seedChart, loading } = useCandleAggregator(symbol, candleSeconds);

  React.useEffect(() => {
    const chart = init(containerId, {
      styles: {
        grid: { horizontal: { color: "#1f293b" }, vertical: { color: "#1f293b" } },
        candle: {
          bar: { upColor: "#22c55e", downColor: "#f43f5e", upBorderColor: "#22c55e", downBorderColor: "#f43f5e", upWickColor: "#22c55e", downWickColor: "#f43f5e" },
          priceMark: { last: { upColor: "#22c55e", downColor: "#f43f5e" } },
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
          ${Object.entries(CANDLE_SECONDS_OPTIONS).map(([label, secs]) => html`
            <button key=${label} class=${`btn btn-sm ${candleSeconds === secs ? "active neutral" : ""}`}
                    onClick=${() => setCandleSeconds(secs)}>${label}</button>
          `)}
        </div>
        <div style=${{ flex: 1 }} />
        <button class="btn btn-sm btn-ghost" onClick=${() => setPickerOpen(true)}>Indicators</button>
        <button class="btn btn-sm btn-ghost" onClick=${toggleFullscreen}>${isFullscreen ? "Exit Full Screen" : "Full Screen"}</button>
      </div>
      <div style=${{ position: "relative" }}>
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
