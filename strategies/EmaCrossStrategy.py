"""Example strategy: EMA crossover with a volume filter.

Educational example, not a recommendation. Long-only:
- Enter when EMA(9) crosses above EMA(21) with volume present.
- Exit on the bearish cross.
This is the simplest classic trend entry; it loses money in choppy,
sideways markets — that is exactly what the benchmark comparison in the
backtester will show honestly.
"""
import pandas as pd

from aetherbot.engine.strategy.interface import Strategy
from aetherbot.ta import atr, ema


class EmaCrossStrategy(Strategy):
    timeframe = "1h"
    can_short = False
    minimal_roi = {"0": 0.06, "60": 0.03, "180": 0.01}
    stoploss = -0.08

    def populate_indicators(self, df: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        df["ema_fast"] = ema(df["close"], 9)
        df["ema_slow"] = ema(df["close"], 21)
        df["ema_fast_prev"] = df["ema_fast"].shift(1)
        df["ema_slow_prev"] = df["ema_slow"].shift(1)
        df["atr"] = atr(df, 14)
        return df

    def populate_entry_trend(self, df: pd.DataFrame,
                             metadata: dict) -> pd.DataFrame:
        df.loc[
            (df["ema_fast_prev"] <= df["ema_slow_prev"])
            & (df["ema_fast"] > df["ema_slow"])
            & (df["volume"] > 0),
            "enter_long",
        ] = 1
        return df

    def populate_exit_trend(self, df: pd.DataFrame,
                             metadata: dict) -> pd.DataFrame:
        df.loc[
            (df["ema_fast_prev"] >= df["ema_slow_prev"])
            & (df["ema_fast"] < df["ema_slow"]),
            "exit_long",
        ] = 1
        return df
