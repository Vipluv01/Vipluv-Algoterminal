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
