import React from "react";
import { init, dispose } from "klinecharts";
import { html } from "../html.js";

const CANDLE_SECONDS = 5; // one candle per 5 market ticks (backend ticks ~1/s)

// Aggregates a stream of {price, timestamp} ticks into OHLCV candles,
// bucketed by wall-clock time -- klinecharts wants real timestamps, not
// a tick index, since its x-axis renders actual times.
function useCandleAggregator(symbol) {
  const [ready, setReady] = React.useState(false);
  const candlesRef = React.useRef([]);      // finalized candles
  const currentRef = React.useRef(null);     // in-progress candle
  const bucketStartRef = React.useRef(0);
  const chartApiRef = React.useRef(null);    // set by the chart once mounted

  const onTick = React.useCallback((price) => {
    const now = Date.now();
    const bucket = Math.floor(now / (CANDLE_SECONDS * 1000));

    if (bucketStartRef.current !== bucket) {
      // New bucket: finalize the previous candle (if any), start a new one.
      if (currentRef.current) candlesRef.current.push(currentRef.current);
      bucketStartRef.current = bucket;
      currentRef.current = { timestamp: bucket * CANDLE_SECONDS * 1000, open: price, high: price, low: price, close: price, volume: 1 };
      if (candlesRef.current.length > 300) candlesRef.current.shift();
    } else if (currentRef.current) {
      currentRef.current.high = Math.max(currentRef.current.high, price);
      currentRef.current.low = Math.min(currentRef.current.low, price);
      currentRef.current.close = price;
      currentRef.current.volume += 1;
    } else {
      currentRef.current = { timestamp: bucket * CANDLE_SECONDS * 1000, open: price, high: price, low: price, close: price, volume: 1 };
    }

    if (chartApiRef.current) {
      chartApiRef.current.updateData({ ...currentRef.current });
    }
    setReady(true);
  }, []);

  const seedChart = React.useCallback((chart) => {
    chartApiRef.current = chart;
    const all = currentRef.current ? [...candlesRef.current, currentRef.current] : candlesRef.current;
    if (all.length) chart.applyNewData(all);
  }, []);

  // Reset aggregation whenever the symbol changes -- a new instrument's
  // candles must not be built on top of the previous symbol's history.
  React.useEffect(() => {
    candlesRef.current = [];
    currentRef.current = null;
    bucketStartRef.current = 0;
    setReady(false);
  }, [symbol]);

  return { onTick, seedChart, ready };
}

export function CandleChart({ symbol, price }) {
  const containerId = React.useId().replace(/:/g, "-");
  const chartRef = React.useRef(null);
  const { onTick, seedChart } = useCandleAggregator(symbol);

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
    chart.createIndicator("MA", false, { id: "candle_pane" });
    chart.createIndicator("VOL", false, { id: "volume_pane" });
    chartRef.current = chart;
    seedChart(chart);

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      dispose(containerId);
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  React.useEffect(() => {
    if (price !== null && price !== undefined) onTick(price);
  }, [price, onTick]);

  return html`<div id=${containerId} style=${{ width: "100%", height: "440px" }} />`;
}
