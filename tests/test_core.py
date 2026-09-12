"""Unit tests: indicators, position sizing, risk manager, config gate.

Reference values are hand-calculated standard TA definitions.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aetherbot.config import BotConfig, TradingModeError, load_config
from aetherbot.persistence.models import Trade, make_session
from aetherbot.risk.position_sizing import (stake_fixed,
                                            stake_percentage,
                                            stake_risk_pct)
from aetherbot.ta import atr, ema, rsi

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "reference")


# ---------------- indicators ----------------

class TestIndicators:
    def test_ema_matches_reference(self):
        # hand-computed EMA(3) on 1..6 with alpha=2/(n+1)=0.5:
        # e1=1, e2=3, e3=(4+3)/2... standard ewm recursive formula
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        out = ema(s, 3)
        # hand-computed ewm(alpha=0.5, adjust=False) from e1=1:
        # e2=1.5, e3=2.25, e4=3.125, e5=4.0625, e6=5.03125
        expected = [2.25, 3.125, 4.0625, 5.03125]
        for got, want in zip(out.tolist()[2:], expected):
            assert got == pytest.approx(want, abs=1e-3)

    def test_ema_constant_series_is_constant(self):
        s = pd.Series([42.0] * 10)
        assert (ema(s, 5).dropna() == 42.0).all()

    def test_rsi_all_gains_is_100(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0,
                       10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
        assert rsi(s, 14).iloc[-1] == 100.0

    def test_rsi_known_values(self):
        """Classic Wilder example closes (public domain), checked against
        an INDEPENDENT loop implementation of the same Wilder smoothing."""
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]

        def ref_rsi(closes, period):
            gains = [max(closes[i] - closes[i - 1], 0.0)
                     for i in range(1, len(closes))]
            losses = [max(closes[i - 1] - closes[i], 0.0)
                      for i in range(1, len(closes))]
            ag, al = gains[0], losses[0]
            out = {}
            for i in range(1, len(gains)):
                ag += (gains[i] - ag) / period
                al += (losses[i] - al) / period
                out[i + 1] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
            return out
        out = rsi(pd.Series(closes), 14)
        ref = ref_rsi(closes, 14)
        # warmup strictly NaN, never 100
        assert out.iloc[:13].isna().all()
        for i, v in ref.items():
            if i >= 14:
                assert out.iloc[i] == pytest.approx(v, abs=1e-9)
        assert out.iloc[-1] == pytest.approx(50.657, abs=1e-3)

    def test_atr_positive_on_real_range(self):
        df = pd.DataFrame({
            "high": [2.0, 2.2, 2.1], "low": [1.0, 1.1, 1.2],
            "close": [1.5, 1.8, 1.7]})
        assert (atr(df, 2).dropna() > 0).all()


# ---------------- position sizing ----------------

class TestPositionSizing:
    def test_fixed(self):
        assert stake_fixed(100) == 100
        with pytest.raises(ValueError):
            stake_fixed(0)

    def test_percentage(self):
        assert stake_percentage(10000, 10) == 1000.0
        with pytest.raises(ValueError):
            stake_percentage(-1, 10)

    def test_risk_pct_hand_calculated(self):
        # balance 10000, risk 1% = 100; entry 50, stop 49 -> distance 1
        # amount = 100 / 1 = 100 units; stake = 100 * 50 = 5000
        r = stake_risk_pct(10000, 1.0, 50.0, 49.0)
        assert r.amount == pytest.approx(100.0)
        assert r.stake == pytest.approx(5000.0)
        assert r.stop_distance == pytest.approx(1.0)

    def test_risk_pct_rejects_bad_stop(self):
        with pytest.raises(ValueError):
            stake_risk_pct(10000, 1.0, 50.0, 50.0)  # stop == entry
        with pytest.raises(ValueError):
            stake_risk_pct(10000, 1.0, 50.0, 0)


# ---------------- config live gate ----------------

class TestLiveModeGate:
    def base(self, **mode_kwargs):
        cfg = BotConfig()
        mc = {"dry_run": False}
        mc.update(mode_kwargs)
        cfg.mode = type(cfg.mode)(**mc)
        return cfg

    def test_default_is_dry_run(self):
        assert BotConfig().mode.dry_run is True

    def test_live_refused_without_full_confirmation(self):
        # dry_run false + confirmation incomplete -> refuse
        cfg = self.base()
        with pytest.raises(TradingModeError):
            cfg.effective_live_mode()

    def test_live_refused_with_partial_confirmation(self):
        cfg = self.base(live_confirmation={
            "confirmed_live": True, "risk_disclosure_accepted": False})
        with pytest.raises(TradingModeError):
            cfg.effective_live_mode()

    def test_live_requires_disclosure_timestamp(self):
        cfg = self.base(live_confirmation={
            "confirmed_live": True, "risk_disclosure_accepted": True})
        with pytest.raises(TradingModeError):  # no timestamp
            cfg.effective_live_mode()

    def test_live_allowed_only_with_full_human_optin(self):
        cfg = self.base(live_confirmation={
            "confirmed_live": True, "risk_disclosure_accepted": True,
            "risk_disclosure_accepted_at": "2026-09-12T00:00:00Z"})
        assert cfg.effective_live_mode() is True

    def test_dry_run_always_effective_regardless(self):
        cfg = BotConfig()
        cfg.mode.live_confirmation = type(cfg.mode.live_confirmation)(
            confirmed_live=True, risk_disclosure_accepted=True,
            risk_disclosure_accepted_at="2026-09-12T00:00:00Z")
        assert cfg.effective_live_mode() is False  # dry_run wins


# ---------------- risk manager ----------------

def make_db(tmp_path):
    return make_session("sqlite:///%s/test.db" % tmp_path)


def make_rm(cfg, session):
    from aetherbot.risk.risk_manager import RiskManager
    return RiskManager(cfg, session,
                       balance_provider=lambda: 10000.0)


def add_closed(session, pnl, close_date, is_win):
    from aetherbot.persistence.models import LossEvent
    t = Trade(pair="X/USDT", side="long", stake=100, amount=1,
              entry_price=100, exit_price=100, pnl=pnl, is_open=False,
              is_win=is_win, strategy="s", exchange="binance",
              mode="dry_run", close_date=close_date)
    session.add(t)
    session.commit()
    if pnl < 0:
        session.add(LossEvent(trade_id=t.id, pnl=pnl, date=close_date))
        session.commit()
    return t


class TestRiskManager:
    def test_max_open_trades(self, tmp_path):
        cfg = BotConfig()
        cfg.trading.max_open_trades = 2
        rm = make_rm(cfg, make_db(tmp_path))
        v = rm.check_entry("A/USDT", 100, 50, 45, open_trades=2)
        assert not v.allowed
        assert any("max_open_trades" in r for r in v.reasons)
        assert rm.check_entry("A/USDT", 100, 50, 45, 1).allowed

    def test_stake_exceeds_balance(self, tmp_path):
        rm = make_rm(BotConfig(), make_db(tmp_path))
        v = rm.check_entry("A/USDT", 20000, 50, 45, 0)
        assert not v.allowed
        assert any("exceeds available balance" in r for r in v.reasons)

    def test_daily_loss_limit_halts_entries(self, tmp_path):
        cfg = BotConfig()
        cfg.risk.daily_loss_limit_pct = 5.0  # 500 on 10k
        session = make_db(tmp_path)
        rm = make_rm(cfg, session)
        now = datetime.now(timezone.utc)
        add_closed(session, -600.0, now - timedelta(minutes=10), False)
        v = rm.check_entry("A/USDT", 100, 50, 45, 0, now=now)
        assert not v.allowed
        assert any("daily loss limit" in r for r in v.reasons)

    def test_max_drawdown_halts_entries(self, tmp_path):
        cfg = BotConfig()
        cfg.risk.max_drawdown_pct = 25.0  # 2500 on 10k
        session = make_db(tmp_path)
        rm = make_rm(cfg, session)
        add_closed(session, -3000.0,
                   datetime.now(timezone.utc) - timedelta(days=3), False)
        v = rm.check_entry("A/USDT", 100, 50, 45, 0)
        assert not v.allowed
        assert any("max drawdown" in r for r in v.reasons)

    def test_cooldown_after_consecutive_losses(self, tmp_path):
        cfg = BotConfig()
        cfg.risk.consecutive_losses = 3
        cfg.risk.cooldown_minutes = 120
        session = make_db(tmp_path)
        rm = make_rm(cfg, session)
        now = datetime.now(timezone.utc)
        for i in range(3):
            add_closed(session, -10.0, now - timedelta(minutes=5 * (i + 1)),
                       False)
        assert rm.consecutive_losses() == 3
        v = rm.check_entry("A/USDT", 100, 50, 45, 0, now=now)
        assert not v.allowed
        assert any("cooldown" in r for r in v.reasons)
        # after cooldown window passes, entries allowed again
        later = now + timedelta(minutes=130)
        assert rm.check_entry("A/USDT", 100, 50, 45, 0,
                              now=later).allowed

    def test_win_resets_loss_streak(self, tmp_path):
        cfg = BotConfig()
        cfg.risk.consecutive_losses = 3
        session = make_db(tmp_path)
        rm = make_rm(cfg, session)
        now = datetime.now(timezone.utc)
        for i in range(2):
            add_closed(session, -10.0, now - timedelta(hours=3), False)
        add_closed(session, +5.0, now - timedelta(minutes=60), True)
        add_closed(session, -10.0, now - timedelta(minutes=10), False)
        assert rm.consecutive_losses() == 1  # streak reset by the win

    def test_initial_stop_long(self, tmp_path):
        rm = make_rm(BotConfig(), make_db(tmp_path))
        assert rm.initial_stop(100.0) == pytest.approx(90.0)  # -10%

    def test_roi_table(self, tmp_path):
        rm = make_rm(BotConfig(), make_db(tmp_path))
        roi = {"0": 0.04, "30": 0.02, "60": 0.01}
        assert rm.roi_target(100.0, 0, roi) == pytest.approx(104.0)
        assert rm.roi_target(100.0, 45, roi) == pytest.approx(102.0)
        assert rm.roi_target(100.0, 999, roi) == pytest.approx(101.0)

    def test_trailing_stop(self, tmp_path):
        cfg = BotConfig()
        cfg.risk.trailing_stop_positive = 0.015
        cfg.risk.trailing_stop_positive_offset = 0.03
        rm = make_rm(cfg, make_db(tmp_path))
        t = Trade(pair="X/USDT", side="long", stake=100, amount=1,
                  entry_price=100, stop_loss=90, is_open=True,
                  strategy="s", exchange="e", mode="dry_run")
        assert rm.update_trailing_stop(t, 101.0) is None  # not armed yet
        new = rm.update_trailing_stop(t, 104.0)            # +4% -> armed
        assert new == pytest.approx(104.0 * 0.985)
        t.stop_loss = new  # engine ratchets the stop; ours only moves up
        assert rm.update_trailing_stop(t, 103.0) is None   # no ratchet down
        better = rm.update_trailing_stop(t, 106.0)
        assert better == pytest.approx(106.0 * 0.985)

    def test_bad_stoploss_config_rejected(self, tmp_path):
        cfg = BotConfig()
        cfg.risk.stoploss = 0.10  # positive = invalid
        rm = make_rm(cfg, make_db(tmp_path))
        v = rm.check_entry("A/USDT", 100, 50, 45, 0)
        assert not v.allowed
        assert any("stoploss must be negative" in r for r in v.reasons)
