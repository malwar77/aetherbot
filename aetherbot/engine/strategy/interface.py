"""Strategy base class — Freqtrade-style interface.

Subclass and implement populate_indicators / populate_entry_trend /
populate_exit_trend. Conventions (Freqtrade-compatible column names):
    enter_long  — bool column, an entry signal on this candle
    exit_long   — bool column, an exit signal on this candle
The LAST closed candle's signal is acted on — the engine never uses the
still-forming candle (no lookahead).

Overridable extras: custom_stoploss, custom_stake_amount, leverage,
minimal_roi, use_ml_predict (opt-in to the AI module's prediction column).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


class Strategy:
    # ---- required attributes (sane defaults) ----
    timeframe: str = "15m"
    can_short: bool = False
    # minutes -> roi fraction. Trade exits at the best threshold reached.
    minimal_roi: dict[str, float] = {"0": 0.04, "30": 0.02, "60": 0.01}
    # static stoploss fraction (e.g. -0.10). Must be negative.
    stoploss: float = -0.10
    process_only_new_candles: bool = True
    # opt-in: merge aetherbot.ai.ml prediction column (ml_predict) in
    # populate_indicators output
    use_ml_predict: bool = False

    # ---- required methods ----
    def populate_indicators(self, dataframe: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        raise NotImplementedError

    def populate_entry_trend(self, dataframe: pd.DataFrame,
                             metadata: dict) -> pd.DataFrame:
        raise NotImplementedError

    def populate_exit_trend(self, dataframe: pd.DataFrame,
                            metadata: dict) -> pd.DataFrame:
        raise NotImplementedError

    # ---- optional overrides ----
    def custom_stoploss(self, pair: str, trade, current_time,
                        current_rate: float, current_profit: float) \
            -> Optional[float]:
        """Return a NEW stoploss fraction (negative, relative to entry) or
        None to keep the config/strategy stoploss."""
        return None

    def custom_stake_amount(self, pair: str, balance: float,
                            proposed_stake: float) -> float:
        return proposed_stake

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float) -> float:
        return proposed_leverage

    # ---- helpers for subclass authors ----
    @staticmethod
    def column(df: pd.DataFrame, name: str) -> pd.Series:
        return df[name] if name in df else pd.Series(
            False, index=df.index)

    def advice(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Full populate pipeline; returns the final dataframe."""
        meta = {"pair": None}
        df = self.populate_indicators(dataframe.copy(), meta)
        df = self.populate_entry_trend(df, meta)
        df = self.populate_exit_trend(df, meta)
        df["enter_long"] = self.column(df, "enter_long").fillna(
            False).astype(bool)
        df["exit_long"] = self.column(df, "exit_long").fillna(
            False).astype(bool)
        return df
