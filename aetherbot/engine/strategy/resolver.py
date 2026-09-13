"""Strategy resolver — load strategies from the /strategies folder.

Drop a `class MyStrategy(Strategy)` in strategies/MyStrategy.py and run
with --strategy MyStrategy. No magic: one module, one class.
"""
from __future__ import annotations

import importlib.util
import os


def load_strategy(name: str, strategy_dir: str = "strategies"):
    path = os.path.join(strategy_dir, "%s.py" % name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "strategy %r not found at %s (create one with `aetherbot "
            "create-strategy %s`)" % (name, path, name))
    spec = importlib.util.spec_from_file_location("aetherbot_strategy_%s"
                                                  % name, path)
    import sys
    module = importlib.util.module_from_spec(spec)
    # register in sys.modules so inspect.getsource works on classes
    # loaded this way (needed by the freqtrade bridge exporter)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for attr in dir(module):
        obj = getattr(module, attr)
        if (isinstance(obj, type) and attr.lower() == name.lower()):
            return obj()
    # fallback: first Strategy subclass found
    from .interface import Strategy
    for attr in dir(module):
        obj = getattr(module, attr)
        if (isinstance(obj, type) and issubclass(obj, Strategy)
                and obj is not Strategy):
            return obj()
    raise ValueError("no Strategy subclass found in %s" % path)


TEMPLATE = '''"""{name} — AetherBot strategy (auto-generated template)."""
import pandas as pd

from aetherbot.engine.strategy.interface import Strategy
from aetherbot.ta import ema, rsi, atr


class {name}(Strategy):
    timeframe = "15m"
    can_short = False
    minimal_roi = {{"0": 0.04, "30": 0.02, "60": 0.01}}
    stoploss = -0.10

    def populate_indicators(self, df: pd.DataFrame, metadata: dict) \\
            -> pd.DataFrame:
        df["ema_fast"] = ema(df["close"], 9)
        df["ema_slow"] = ema(df["close"], 21)
        df["rsi"] = rsi(df["close"], 14)
        df["atr"] = atr(df, 14)
        return df

    def populate_entry_trend(self, df: pd.DataFrame, metadata: dict) \\
            -> pd.DataFrame:
        df.loc[
            (df["ema_fast"] > df["ema_slow"])
            & (df["rsi"] < 70)
            & (df["close"] > df["ema_fast"])
            & (df["volume"] > 0),
            "enter_long",
        ] = 1
        return df

    def populate_exit_trend(self, df: pd.DataFrame, metadata: dict) \\
            -> pd.DataFrame:
        df.loc[
            (df["ema_fast"] < df["ema_slow"])
            | (df["rsi"] > 80),
            "exit_long",
        ] = 1
        return df
'''


def create_strategy(name: str, strategy_dir: str = "strategies") -> str:
    os.makedirs(strategy_dir, exist_ok=True)
    path = os.path.join(strategy_dir, "%s.py" % name)
    if os.path.exists(path):
        raise FileExistsError("strategy already exists: %s" % path)
    with open(path, "w") as f:
        f.write(TEMPLATE.format(name=name))
    return path
