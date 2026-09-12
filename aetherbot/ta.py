"""Technical indicators — pure pandas implementations.

Kept dependency-light (no TA-Lib C requirement). Every function is
deterministic and unit-tested against hand-computed reference values.
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (standard alpha=2/(n+1))."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI with Wilder smoothing (public-domain TA definition).

    Warmup: the first `period` values are NaN (undefined). A window with
    zero average loss yields RSI 100."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    # no losses at all in the window -> RSI 100 (pure gains)
    out = out.where(avg_loss > 0, 100.0)
    # warmup window is undefined, NOT 100
    out.iloc[:period] = float("nan")
    return out


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing). df needs high/low/close."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
