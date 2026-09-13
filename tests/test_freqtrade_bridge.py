"""Freqtrade bridge tests.

Hand-verified expectations:
- a direct Strategy subclass exports to a compilable IStrategy file
- the generated source re-bases the class and adds INTERFACE_VERSION = 3
- the user's own method/attribute content survives the export verbatim
- non-Strategy classes and indirect subclasses are refused
- the generated file contains the license-boundary note
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aetherbot.engine.strategy.interface import Strategy
from aetherbot.freqtrade_bridge import export_to_freqtrade


class SampleStrategy(Strategy):
    """Sample strategy for export tests."""
    timeframe = "1h"
    can_short = False
    stoploss = -0.05

    def populate_indicators(self, dataframe, metadata):
        dataframe["rsi"] = 42.0
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe["enter_long"] = False
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = False
        return dataframe


class IndirectStrategy(SampleStrategy):
    pass


class NotAStrategy:
    pass


class BridgeTests(unittest.TestCase):
    def test_export_compiles_and_rebases(self):
        source = export_to_freqtrade(SampleStrategy)
        compile(source, "freqtrade_SampleStrategy.py", "exec")
        self.assertIn("class SampleStrategy(IStrategy):", source)
        self.assertIn("INTERFACE_VERSION = 3", source)
        self.assertIn("from freqtrade.strategy import IStrategy",
                      source)

    def test_user_code_survives_verbatim(self):
        source = export_to_freqtrade(SampleStrategy)
        self.assertIn('dataframe["rsi"] = 42.0', source)
        self.assertIn('timeframe = "1h"', source)
        self.assertIn("stoploss = -0.05", source)
        self.assertIn("populate_entry_trend", source)

    def test_license_note_present(self):
        source = export_to_freqtrade(SampleStrategy)
        self.assertIn("code was copied", source)
        self.assertIn("No freqtrade", source)
        self.assertIn("TOTAL LOSS", source)

    def test_writes_file(self):
        import tempfile
        from pathlib import Path
        out = Path(tempfile.mkdtemp()) / "out.py"
        source = export_to_freqtrade(SampleStrategy,
                                     out_path=str(out))
        self.assertEqual(out.read_text(), source)
        compile(source, str(out), "exec")

    def test_refuses_non_strategy(self):
        with self.assertRaises(TypeError):
            export_to_freqtrade(NotAStrategy)

    def test_refuses_indirect_subclass(self):
        with self.assertRaises(ValueError):
            export_to_freqtrade(IndirectStrategy)


if __name__ == "__main__":
    unittest.main()
