"""Example strategy: RSI + EMA crossover.

Educational example, not a recommendation. Long-only:
- Enter when the fast EMA is above the slow EMA (trend up) AND RSI(14)
  crosses up out of oversold (<40 -> >40) — a pullback-resume entry.
- Exit on EMA bearish cross, or RSI > 80 (stretched).
Stop/ROI come from the strategy attributes below.
"""
import pandas as pd

from aetherbot.engine.strategy.interface import Strategy
from aetherbot.ta import atr, ema, rsi


class RsiEmaCross(Strategy):
    timeframe = "15m"
    can_short = False
    minimal_roi = {"0": 0.04, "30": 0.02, "60": 0.01, "120": 0.005}
    stoploss = -0.10

    def populate_indicators(self, df: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        df["ema_fast"] = ema(df["close"], 9)
        df["ema_slow"] = ema(df["close"], 21)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df, 14)
        df["rsi_prev"] = df["rsi"].shift(1)
        return df

    def populate_entry_trend(self, df: pd.DataFrame,
                             metadata: dict) -> pd.DataFrame:
        df.loc[
            (df["ema_fast"] > df["ema_slow"])
            & (df["rsi_prev"] < 40)
            & (df["rsi"] >= 40)
            & (df["volume"] > 0),
            "enter_long",
        ] = 1
        return df

    def populate_exit_trend(self, df: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        df.loc[
            (df["ema_fast"] < df["ema_slow"])
            | (df["rsi"] > 80),
            "exit_long",
        ] = 1
        return df
