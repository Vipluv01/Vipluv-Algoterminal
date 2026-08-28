"""Technical indicators used by the 4 single-instrument strategies.

Each one is validated against a known-by-construction reference case (the
same discipline sim/bourse_sim/stylized_facts.py uses: prove the
MEASUREMENT is right before trusting what it reports), not just eyeballed
against a plausible-looking chart.
"""

from __future__ import annotations

import numpy as np


def ema(values: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average, pandas'-.ewm(span=...)-compatible
    (alpha = 2/(span+1)), computed by hand since this module has no pandas
    dependency of its own."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI. Returns NaN for indices before `period` deltas exist
    (there is no meaningful RSI yet, and silently returning e.g. 50 there
    would be a fabricated neutral signal, not an honest "not enough data
    yet")."""
    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    out = np.full(len(values), np.nan)
    if len(values) <= period:
        return out

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi_from_avgs(avg_gain, avg_loss)

    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0  # no losses at all in the window -- maximally overbought, not undefined
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(values: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = ema(values, fast) - ema(values, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def bollinger_bands(values: np.ndarray, window: int = 20, n_std: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (lower, mid, upper). NaN before `window` observations exist,
    for the same reason rsi() does -- a band computed from 3 points isn't
    a real band, it's noise dressed up as one."""
    n = len(values)
    mid = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = values[i - window + 1: i + 1]
        m, s = w.mean(), w.std(ddof=0)
        mid[i], lower[i], upper[i] = m, m - n_std * s, m + n_std * s
    return lower, mid, upper


def sma(prices: list[float] | np.ndarray, period: int) -> np.ndarray:
    """Simple moving average. NaN before `period` observations exist, same
    convention as bollinger_bands (its own mid line IS an SMA -- kept
    separate rather than routed through this function, since bollinger_bands
    predates this one and there's no live caller needing them coupled)."""
    values = np.asarray(prices, dtype=float)
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(period - 1, n):
        out[i] = values[i - period + 1: i + 1].mean()
    return out


def vwap(prices: list[float] | np.ndarray, volumes: list[float] | np.ndarray) -> np.ndarray:
    """Cumulative (session-to-date) volume-weighted average price:
    vwap[t] = sum(price[:t+1] * volume[:t+1]) / sum(volume[:t+1]).

    Cumulative, not a rolling window -- this is the conventional definition
    of VWAP (anchored to session start, e.g. a trading day), distinct from
    a rolling volume-weighted mean over a fixed lookback. A step with zero
    volume carries forward the previous value rather than producing NaN or
    a divide-by-zero: zero volume means nothing traded, so the
    volume-weighted average price is unchanged by that step, not undefined.
    """
    p = np.asarray(prices, dtype=float)
    v = np.asarray(volumes, dtype=float)
    if len(p) != len(v):
        raise ValueError(f"prices and volumes must be the same length, got {len(p)} and {len(v)}")

    cum_pv = np.cumsum(p * v)
    cum_v = np.cumsum(v)
    out = np.full(len(p), np.nan)
    nonzero = cum_v > 0
    out[nonzero] = cum_pv[nonzero] / cum_v[nonzero]
    # Forward-fill any leading zero-volume steps: no volume yet means "no
    # VWAP yet" is honestly NaN (nothing to average), but a zero-volume
    # step AFTER trading has started should hold the last real VWAP, not
    # revert to NaN.
    last_valid = np.nan
    for i in range(len(out)):
        if nonzero[i]:
            last_valid = out[i]
        elif not np.isnan(last_valid):
            out[i] = last_valid
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range (Wilder's smoothing, same recurrence rsi() uses
    for its own averages -- an exponential-like running average with
    smoothing factor 1/period, not a plain rolling mean).

    True range at t is the largest of: high-low, |high - prev_close|,
    |low - prev_close| -- the second and third terms are what make this
    different from a plain high-low range, capturing a gap through the
    previous close as real volatility rather than invisible to the
    measure. NaN for index 0 (no previous close exists) and for the
    `period` warm-up, same convention as rsi().
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(high)
    if not (len(low) == len(close) == n):
        raise ValueError("high, low, and close must be the same length")

    out = np.full(n, np.nan)
    if n < 2:
        return out

    true_range = np.full(n, np.nan)
    true_range[1:] = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )

    if n <= period:
        return out

    # First ATR value is a plain mean of the first `period` true ranges
    # (indices 1..period, since index 0 has none) -- Wilder's own
    # convention for seeding the running average, same as rsi()'s
    # avg_gain/avg_loss seed.
    out[period] = true_range[1: period + 1].mean()
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + true_range[i]) / period
    return out


def bollinger_bandwidth(prices: list[float] | np.ndarray, period: int = 20, num_std: float = 2.0) -> np.ndarray:
    """Normalized band width: (upper - lower) / mid -- a squeeze/expansion
    measure, not the bands themselves. A falling bandwidth is the textbook
    "volatility contraction, breakout likely soon" signal bb_squeeze
    (Phase 3's new strategy) watches for; the raw bands alone don't
    directly expose that as a single comparable number across time."""
    lower, mid, upper = bollinger_bands(np.asarray(prices, dtype=float), window=period, n_std=num_std)
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = np.where(mid != 0, (upper - lower) / mid, np.nan)
    return bandwidth
