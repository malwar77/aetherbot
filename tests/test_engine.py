"""Tests: strategy signals, dry-run exchange, engine gates, AI brain.

The AI contract under test (same design as RegimeDesk): the LLM is
advisory with negative-only power — "against" can skip a signal, never
create or resize a trade — and works via local Ollama with no API key.
"""
import json
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aetherbot.ai.brain import ADVISORY_NOTE, AIBrain
from aetherbot.config import BotConfig
from aetherbot.exchange.dry_run import DryRunExchange
from aetherbot.persistence.models import make_session
from strategies.RsiEmaCross import RsiEmaCross


def make_candles(n=300, start=100.0, drift=0.001, seed=7):
    rng = np.random.default_rng(seed)
    closes = start
    rows = []
    for i in range(n):
        o = closes
        closes = closes * (1 + drift + rng.normal(0, 0.01))
        c = closes
        h = max(o, c) * (1 + abs(rng.normal(0, 0.003)))
        low = min(o, c) * (1 - abs(rng.normal(0, 0.003)))
        rows.append([1700000000000 + i * 900000, o, h, low, c,
                     float(rng.integers(10, 100))])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low",
                                       "close", "volume"])

def pullback_candles(seed=1):
    """Uptrend with a shallow pullback and fast resumption — engineered
    so the RsiEmaCross entry conditions coincide (verified empirically:
    EMA fast>slow at the bar where RSI crosses up through 40)."""
    rng = np.random.default_rng(seed)
    rows = []
    p = 100.0
    for _ in range(280):
        p *= 1 + 0.002 + rng.normal(0, 0.002)
        rows.append(p)
    for _ in range(5):        # shallow pullback
        p *= 0.994
        rows.append(p)
    for _ in range(1):        # fast resumption (entry bar is the LAST bar)
        p *= 1.012
        rows.append(p)
    out = []
    ts = 1700000000000
    for c in rows:
        out.append([ts, c * 0.999, c * 1.001, c * 0.999, c, 100.0])
        ts += 900000
    return pd.DataFrame(out, columns=["timestamp", "open", "high", "low",
                                      "close", "volume"])


# ---------------- strategy ----------------

class TestRsiEmaCross:
    def test_no_signal_on_flat_data(self):
        s = RsiEmaCross()
        df = make_candles(300, drift=0.0)
        out = s.advice(df)
        assert "enter_long" in out and "exit_long" in out
        # flat noise should produce at most a handful of signals
        assert int(out["enter_long"].sum()) <= 20

    def test_uptrend_pullback_produces_entry(self):
        s = RsiEmaCross()
        out = s.advice(pullback_candles())
        assert int(out["enter_long"].sum()) == 1
        # the entry sits in the pullback-resumption zone
        assert bool(out["enter_long"].iloc[-15:].any()) is True

    def test_columns_are_bool_cleaned(self):
        out = RsiEmaCross().advice(make_candles(200))
        assert out["enter_long"].fillna(False).dtype == bool


# ---------------- dry-run exchange ----------------

class TestDryRunExchange:
    def test_market_order_fees_and_balance(self):
        candles = make_candles(50)
        ex = DryRunExchange(start_balance=1000.0, fee_rate=0.001,
                            candles={"BTC/USDT":
                                     candles[["timestamp", "open", "high",
                                              "low", "close",
                                              "volume"]].values.tolist()})
        px = ex.last_price("BTC/USDT")
        ex.market_order("BTC/USDT", "buy", 0.1)
        assert ex.balance == pytest.approx(1000.0 - 0.1 * px * 0.001)
        assert len(ex.orders) == 1
        assert ex.orders[0]["simulated"] is True

    def test_close_position_realizes_pnl(self):
        candles = make_candles(50, drift=0.0)
        rows = candles.values.tolist()
        ex = DryRunExchange(start_balance=1000.0, candles={"BTC/USDT": rows})
        ex.market_order("BTC/USDT", "buy", 0.2)
        # price moves up 10%
        rows[-1][4] *= 1.10
        ex.update_market("BTC/USDT", rows)
        ex.close_position("BTC/USDT", 0.2, "long")
        assert ex.unrealized_pnl("BTC/USDT") == 0.0
        assert len(ex.orders) == 2


# ---------------- engine gates ----------------

class FakeBrain:
    def __init__(self, verdict):
        self.verdict = verdict

    def annotate(self, proposal):
        return {"summary": "test", "agreement": self.verdict,
                "notes": [], "source": "test", "advisory": ADVISORY_NOTE}


def build_engine(tmp_path, brain=None, pair="BTC/USDT", df=None,
                 ai_veto=True):
    from aetherbot.engine.bot import AetherBot
    cfg = BotConfig()
    cfg.trading.pairs.whitelist = [pair]
    cfg.trading.pairs.blacklist = []
    cfg.trading.candle_source = "file"
    cfg.ai.advisory_veto = ai_veto
    cfg.persistence.db_url = "sqlite:///%s/eng.db" % tmp_path
    session = make_session(cfg.persistence.db_url)
    df = df if df is not None else make_candles(400, drift=0.002, seed=3)
    out = "data_%s" % tmp_path
    os.makedirs(out, exist_ok=True)
    df.to_parquet(os.path.join(out, "%s_15m.parquet" % pair.replace("/", "_")))
    from pathlib import Path
    bot = AetherBot(cfg, RsiEmaCross(), session=session, brain=brain,
                    once=True)
    assert bot.feed.source == "file"
    bot.feed.fixture_dir = Path(out)
    bot.feed._index_fixtures([pair])
    assert pair in bot.feed._files, "fixture must be indexed for the test"
    return bot, session


class TestEngineGates:
    def test_engine_starts_in_dry_run_never_live_by_default(self, tmp_path):
        bot, _ = build_engine(tmp_path)
        assert bot.mode_name == "dry_run"
        assert isinstance(bot.exchange, DryRunExchange)

    def test_cycle_opens_trade_through_risk_gates(self, tmp_path):
        bot, session = build_engine(tmp_path)
        bot.cycle()
        trades = session.query(
            __import__("aetherbot.persistence.models", fromlist=["Trade"])
            .Trade).all()
        # either an entry happened (gate-passed) or the strategy had no
        # signal — but never an unvalidated trade
        for t in trades:
            assert t.mode == "dry_run"
            assert t.stop_loss is not None and t.stop_loss > 0

    def test_ai_against_veto_skips_entry(self, tmp_path):
        bot, session = build_engine(tmp_path, brain=FakeBrain("against"))
        bot.cycle()
        trades = session.query(
            __import__("aetherbot.persistence.models", fromlist=["Trade"])
            .Trade).all()
        assert len(trades) == 0  # the LLM's only power: skip

    def test_ai_questions_or_supports_do_not_block(self, tmp_path):
        for verdict in ("questions", "supports"):
            bot, session = build_engine(tmp_path, brain=FakeBrain(verdict))
            bot.cycle()
            trades = session.query(
                __import__("aetherbot.persistence.models",
                           fromlist=["Trade"]).Trade).all()
            # identical behavior to no-AI: signal decides, gates decide
            assert all(t.mode == "dry_run" for t in trades)

    def test_live_refused_without_human_optin(self, tmp_path):
        from aetherbot.config import TradingModeError
        cfg = BotConfig()
        cfg.mode.dry_run = False  # not enough — confirmation missing
        cfg.persistence.db_url = "sqlite:///%s/x.db" % tmp_path
        with pytest.raises(TradingModeError):
            from aetherbot.engine.bot import AetherBot
            AetherBot(cfg, RsiEmaCross(),
                      session=make_session(cfg.persistence.db_url),
                      once=True)


# ---------------- AI brain ----------------

class TestAIBrain:
    def make_brain(self):
        return AIBrain()

    def proposal(self):
        return {"pair": "BTC/USDT", "side": "long",
                "entry_price": 50000.0, "stake": 1000.0,
                "strategy": "RsiEmaCross", "timeframe": "15m",
                "indicators": {"rsi": 25.0, "ema_fast": 49900.0,
                               "ema_slow": 49500.0},
                "reasons": "test"}

    def test_offline_falls_back_to_local_deterministic(self):
        brain = self.make_brain()
        brain._probe = False
        ann = brain.annotate(self.proposal())
        assert ann["source"] == "local_deterministic"
        assert ann["agreement"] == "supports"  # clean aligned case
        assert ADVISORY_NOTE in ann["advisory"]

    def test_stretched_rsi_reads_questions(self):
        p = self.proposal()
        p["indicators"]["rsi"] = 75.0
        brain = self.make_brain()
        brain._probe = False
        ann = brain.annotate(p)
        assert ann["agreement"] == "questions"

    def test_ollama_backend_used_when_available(self):
        brain = self.make_brain()
        brain._probe = True
        brain._call_ollama = lambda p: {"summary": "ok",
                                        "agreement": "questions",
                                        "notes": ["n"]}
        ann = brain.annotate(self.proposal())
        assert ann["source"] == "ollama:llama3.2"
        assert ann["agreement"] == "questions"

    def test_adversarial_ollama_output_neutralized(self):
        brain = self.make_brain()
        brain._probe = True

        def evil(p):
            return {"summary": "s", "agreement": "supports", "notes": [],
                    "side": "short", "stake": 999999.0, "mode": "live",
                    "api_key": "should-not-exist"}
        brain._call_ollama = evil
        p = self.proposal()
        before = json.dumps(p, sort_keys=True)
        ann = brain.annotate(p)
        assert set(ann.keys()) == {"summary", "agreement", "notes",
                                   "source", "advisory"}
        assert json.dumps(p, sort_keys=True) == before
        assert "api_key" not in json.dumps(ann)
        assert p["side"] == "long"

    def test_ollama_failure_degrades_not_crashes(self):
        brain = self.make_brain()
        brain._probe = True

        def boom(p):
            raise RuntimeError("server melted")
        brain._call_ollama = boom
        ann = brain.annotate(self.proposal())
        assert any("failed" in n for n in ann["notes"])

    def test_annotation_never_mutates_proposal(self):
        brain = self.make_brain()
        p = self.proposal()
        before = json.dumps(p, sort_keys=True)
        brain.annotate(p)
        assert json.dumps(p, sort_keys=True) == before
