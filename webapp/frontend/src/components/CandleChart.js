import React from "react";
import { init, dispose } from "klinecharts";
import { html } from "../html.js";

const CANDLE_SECONDS_OPTIONS = { "1s": 1, "5s": 5, "1m": 60, "5m": 300 };

const MAIN_INDICATORS = ["MA", "EMA", "BOLL", "SAR", "BBI"];
const SUB_INDICATORS = ["VOL", "MACD", "RSI", "KDJ"];

// Aggregates a stream of {price, timestamp} ticks into OHLCV candles,
// bucketed by wall-clock time -- klinecharts wants real timestamps, not
// a tick index, since its x-axis renders actual times.
function useCandleAggregator(symbol, candleSeconds) {
  const [ready, setReady] = React.useState(false);
  const candlesRef = React.useRef([]);      // finalized candles
  const currentRef = React.useRef(null);     // in-progress candle
  const bucketStartRef = React.useRef(0);
  const chartApiRef = React.useRef(null);    // set by the chart once mounted

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
    setReady(true);
  }, [candleSeconds]);

  const seedChart = React.useCallback((chart) => {
    chartApiRef.current = chart;
    const all = currentRef.current ? [...candlesRef.current, currentRef.current] : candlesRef.current;
    if (all.length) chart.applyNewData(all);
  }, []);

  // Reset aggregation whenever the symbol OR the candle bucket size
  // changes -- a 1m candle series built out of 5s-bucketed data (or a new
  // instrument's ticks) would just be wrong, not merely coarser.
  React.useEffect(() => {
    candlesRef.current = [];
    currentRef.current = null;
    bucketStartRef.current = 0;
    setReady(false);
  }, [symbol, candleSeconds]);

  return { onTick, seedChart, ready };
}

function IndicatorPicker({ active, onToggle, onClose }) {
  return html`
    <div class="indicator-picker-backdrop" onClick=${onClose}>
      <div class="indicator-picker" onClick=${(e) => e.stopPropagation()}>
        <div class="indicator-picker-header">
          <span>Indicators</span>
          <button class="btn btn-sm btn-ghost" onClick=${onClose}>✕</button>
        </div>
        <div class="indicator-picker-section">Main (overlay)</div>
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
      </div>
    </div>
  `;
}

export function CandleChart({ symbol, price, height = "440px" }) {
  const containerId = React.useId().replace(/:/g, "-");
  const wrapRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const paneIdByIndicatorRef = React.useRef({}); // name -> paneId, needed to removeIndicator later
  const [candleSeconds, setCandleSeconds] = React.useState(CANDLE_SECONDS_OPTIONS["5s"]);
  const [active, setActive] = React.useState(() => new Set(["MA", "VOL"]));
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [isFullscreen, setIsFullscreen] = React.useState(false);
  const { onTick, seedChart } = useCandleAggregator(symbol, candleSeconds);

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
    return () => {
      window.removeEventListener("resize", onResize);
      dispose(containerId);
      chartRef.current = null;
      paneIdByIndicatorRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

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
      <div id=${containerId} style=${{ width: "100%", height: isFullscreen ? "calc(100vh - 40px)" : height }} />
      ${pickerOpen && html`<${IndicatorPicker} active=${active} onToggle=${toggleIndicator} onClose=${() => setPickerOpen(false)} />`}
    </div>
  `;
}
