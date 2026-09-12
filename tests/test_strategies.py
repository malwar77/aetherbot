"""Tests: the shipped example strategies.

Reference values are computed independently in each test (first
principles or direct pandas equivalents), never by re-calling the
strategy logic under test.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aetherbot.ta import ema
from strategies.DonchianBreakout import DonchianBreakout
from strategies.EmaCrossStrategy import EmaCrossStrategy
from strategies.RsiEmaCross import RsiEmaCross


def trending_candles(n=300, start=100.0, drift=0.002, seed=3):
    """Smooth deterministic uptrend: every bar closes above the last."""
    rows = []
    price = start
    for i in range(n):
        o = price
        price = price * (1 + drift)
        c = price
        rows.append([1700000000000 + i * 3600000, o, c * 1.001,
                     o * 0.999, c, 50.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high",
                                       "low", "close", "volume"])


def sideways_candles(n=300, seed=3):
    """Alternating up/down closes around 100 — a chop series."""
    rows = []
    price = 100.0
    for i in range(n):
        o = price
        price = 100.0 + (2.0 if i % 2 == 0 else -2.0)
        c = price
        rows.append([1700000000000 + i * 3600000, o, max(o, c) * 1.001,
                     min(o, c) * 0.999, c, 50.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high",
                                       "low", "close", "volume"])


# ---------------------------------------------------------------------------
# EmaCrossStrategy
# ---------------------------------------------------------------------------
def test_ema_cross_enters_on_bullish_cross_not_earlier():
    df = sideways_candles(120)
    out = EmaCrossStrategy().advice(df)
    # independent reference: recompute the cross condition from raw closes
    fast = ema(df["close"], 9)
    slow = ema(df["close"], 21)
    expected = ((fast.shift(1) <= slow.shift(1))
                & (fast > slow)).fillna(False)
    got = out["enter_long"].fillna(False)
    # where the independent condition holds, the strategy must mark entry
    assert (got[expected] == True).all() if expected.any() else True
    assert (got & ~expected).sum() == 0   # no entries outside the condition


def test_ema_cross_steady_uptrend_has_no_false_exits():
    df = trending_candles(200)
    out = EmaCrossStrategy().advice(df)
    # in a monotonic uptrend EMA9 stays above EMA21 once warmed up:
    # no exit signals may fire after indicator warmup (bar 21)
    assert not out["exit_long"].iloc[21:].any()
    # and entries can only be the initial cross, never more
    assert out["enter_long"].sum() <= 1


def test_ema_cross_volume_gate_blocks_zero_volume():
    df = trending_candles(200)
    df["volume"] = 0.0
    out = EmaCrossStrategy().advice(df)
    assert not out["enter_long"].any()


# ---------------------------------------------------------------------------
# DonchianBreakout — hand-computed reference
# ---------------------------------------------------------------------------
def test_donchian_breakout_hand_computed_entry():
    # Build 30 flat bars at 100 (highs 100.5), then one bar closing 101.
    rows = []
    for i in range(30):
        rows.append([1700000000000 + i * 14400000, 100.0, 100.5,
                     99.5, 100.0, 10.0])
    # bar 30: high spike — close must EXCEED the prior-20-bar high (100.5)
    rows.append([1700000000000 + 30 * 14400000, 100.0, 101.0,
                 99.5, 101.0, 10.0])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high",
                                     "low", "close", "volume"])
    out = DonchianBreakout().advice(df)
    # hand check: don_high at bar 30 = max high of bars 10..29 = 100.5,
    # close 101.0 > 100.5 -> entry exactly there, nowhere else
    assert out["enter_long"].iloc[30] == True
    assert out["enter_long"].sum() == 1


def test_donchian_breakout_no_lookdown_channel_is_prior_bars_only():
    df = trending_candles(60)
    out = DonchianBreakout().advice(df)
    df2 = df.iloc[:-1].copy()          # drop the last bar entirely
    out2 = DonchianBreakout().advice(df2)
    # signals on bars 0..58 must be identical — the last bar cannot
    # change earlier signals (channel uses shift(1))
    assert (out["enter_long"].iloc[:-1] == out2["enter_long"]).all()


def test_donchian_breakout_flat_market_never_enters():
    df = sideways_candles(120)          # oscillates 98-102 forever
    out = DonchianBreakout().advice(df)
    # channel high includes the 102 spikes -> close never exceeds it
    assert not out["enter_long"].any()


# ---------------------------------------------------------------------------
# RsiEmaCross (existing) — regression check that all three load via advice
# ---------------------------------------------------------------------------
def test_rsi_ema_cross_advice_runs_and_labels():
    df = sideways_candles(200)
    out = RsiEmaCross().advice(df)
    for col in ("ema_fast", "ema_slow", "rsi"):
        assert col in out
    assert set(out["enter_long"].unique()) <= {True, False}
