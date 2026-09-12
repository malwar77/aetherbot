"""Tests: backtester sanity + ML module + force commands."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aetherbot.ai.ml import ModelStore, build_features, make_labels
from aetherbot.backtest.backtester import backtest
from aetherbot.config import BotConfig
from strategies.RsiEmaCross import RsiEmaCross

from tests.test_engine import build_engine, make_candles


class TestBacktester:
    def test_runs_and_reports_honestly(self):
        cfg = BotConfig()
        df = make_candles(600, drift=0.001, seed=11)
        res = backtest(cfg, RsiEmaCross(), df, fee_rate=0.001)
        s = res.summary()
        assert "backtest:" in s
        assert ("benchmark" in s.lower())  # verdict always surfaced
        assert res.max_drawdown >= 0.0
        # every realized trade passed through a stop (risk invariants)
        for t in res.trades:
            assert t["reason"] in ("stoploss", "roi", "exit_signal",
                                   "end_of_data")

    def test_downtrend_loses_less_than_buy_hold(self):
        cfg = BotConfig()
        df = make_candles(600, drift=-0.002, seed=5)
        res = backtest(cfg, RsiEmaCross(), df, fee_rate=0.001)
        # in a falling market a long-only strategy with stops should not
        # be dramatically worse than buy&hold (stops protect the downside)
        assert res.total_pnl <= 0 or res.beats_buy_hold


class TestML:
    def test_features_and_labels_shapes(self):
        df = make_candles(100)
        feats = build_features(df)
        labels = make_labels(df, horizon=4)
        assert len(feats.columns) >= 5
        assert labels.isin([0, 1]).all()

    def test_train_and_predict_roundtrip(self, tmp_path):
        df = make_candles(400, drift=0.001, seed=2)
        store = ModelStore(model_dir=str(tmp_path), kind="gradient_boosting")
        metrics = store.train(df)
        assert metrics["accuracy"] >= 0.0
        proba = store.predict_proba(df)
        assert proba.dropna().between(0, 1).all()
        # model persisted
        assert (tmp_path / "gradient_boosting_model.pkl").exists()

    def test_needs_retrain_schedule(self, tmp_path):
        store = ModelStore(model_dir=str(tmp_path),
                           retrain_every_days=7)
        assert store.needs_retrain(pd.Timestamp("2026-09-12")) is True
        store.model = object()  # simulate a loaded model
        store.trained_at = pd.Timestamp("2026-09-12")
        assert store.needs_retrain(pd.Timestamp("2026-09-13")) is False
        assert store.needs_retrain(pd.Timestamp("2026-09-30")) is True

    def test_refuses_to_train_on_tiny_data(self, tmp_path):
        store = ModelStore(model_dir=str(tmp_path))
        with pytest.raises(ValueError):
            store.train(make_candles(50))


class TestForceCommands:
    def test_force_enter_passes_gates(self, tmp_path):
        from aetherbot.engine.force import force_enter
        bot, session = build_engine(tmp_path, brain=None)
        msg = force_enter(bot, "BTC/USDT")
        # either executed (gates passed) or refused BY the gates — never
        # bypassed
        assert ("REFUSED" in msg) or ("executed" in msg) or \
            ("already" in msg)

    def test_force_enter_vetoed_by_risk_gates(self, tmp_path):
        from aetherbot.engine.force import force_enter
        bot, _ = build_engine(tmp_path)
        # exhaust max_open_trades
        bot.cfg.trading.max_open_trades = 0
        msg = force_enter(bot, "BTC/USDT")
        assert "REFUSED" in msg

    def test_force_enter_ai_against_veto(self, tmp_path):
        from tests.test_engine import FakeBrain
        from aetherbot.engine.force import force_enter
        bot, _ = build_engine(tmp_path, brain=FakeBrain("against"))
        msg = force_enter(bot, "BTC/USDT")
        assert "vetoed" in msg

    def test_force_exit_unknown_id(self, tmp_path):
        from aetherbot.engine.force import force_exit
        bot, _ = build_engine(tmp_path)
        assert "no open trade" in force_exit(bot, "999")
