"""Example strategy: Donchian channel breakout (Turtle-style entry).

Educational example, not a recommendation. Long-only:
- Enter when close breaks above the highest high of the previous
  `entry_lookback` bars (the breakout bar itself is EXCLUDED from the
  channel window — no lookahead at its own close).
- Exit when close breaks below the lowest low of the previous
  `exit_lookback` bars.
Breakout systems trend-follow: long flat stretches between big wins,
which the backtester reports honestly.
"""
import pandas as pd

from aetherbot.engine.strategy.interface import Strategy
from aetherbot.ta import atr


class DonchianBreakout(Strategy):
    timeframe = "4h"
    can_short = False
    minimal_roi = {"0": 0.10, "240": 0.05, "720": 0.02}
    stoploss = -0.10
    entry_lookback = 20
    exit_lookback = 10

    def populate_indicators(self, df: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        # shift(1): channel built ONLY from strictly earlier bars
        df["don_high"] = df["high"].rolling(
            self.entry_lookback).max().shift(1)
        df["don_low"] = df["low"].rolling(
            self.exit_lookback).min().shift(1)
        df["atr"] = atr(df, 14)
        return df

    def populate_entry_trend(self, df: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        df.loc[
            (df["close"] > df["don_high"])
            & (df["volume"] > 0),
            "enter_long",
        ] = 1
        return df

    def populate_exit_trend(self, df: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        df.loc[
            (df["close"] < df["don_low"]),
            "exit_long",
        ] = 1
        return df
