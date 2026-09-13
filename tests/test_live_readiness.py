"""Tests: live-trading readiness + per-order live warning.

Hand-verified expectations. The audit is read-only by construction;
the live_confirmation fields are human-only and the engine warning is
informational (never blocks or alters an order)."""
import logging
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aetherbot.config import BotConfig, LiveConfirmation, ModeConfig
from aetherbot.live_readiness import LiveReadiness, RISK_DISCLOSURE
from aetherbot.persistence.models import make_session
from aetherbot.engine.bot import AetherBot
from strategies.RsiEmaCross import RsiEmaCross


def live_conf():
    return LiveConfirmation(
        confirmed_live=True, risk_disclosure_accepted=True,
        risk_disclosure_accepted_at=datetime(2026, 9, 13, tzinfo=timezone.utc))


def test_default_config_is_not_ready():
    # dry_run is the hard default — audit must refuse it
    lr = LiveReadiness(BotConfig())
    assert lr.ready is False
    checks = {n: ok for n, ok, _ in lr.checks()}
    assert checks["dry_run is false"] is False
    assert checks["live confirmation present"] is False


def test_fully_live_config_is_ready():
    cfg = BotConfig()
    cfg.mode = ModeConfig(dry_run=False, live_confirmation=live_conf())
    cfg.trading.pairs.whitelist = ["BTC/USDT"]
    cfg.trading.pairs.blacklist = []
    lr = LiveReadiness(cfg)
    checks = {n: ok for n, ok, _ in lr.checks()}
    assert all(checks.values()), [d for n, ok, d in lr.checks() if not ok]
    assert lr.ready is True
    assert lr.report()["ready"] is True


def test_missing_timestamp_still_fails():
    # disclosure accepted but no timestamp — the audit must demand the
    # human left a dated record of acceptance
    cfg = BotConfig()
    cfg.mode = ModeConfig(
        dry_run=False,
        live_confirmation=LiveConfirmation(confirmed_live=True,
                                           risk_disclosure_accepted=True))
    checks = {n: ok for n, ok, _ in LiveReadiness(cfg).checks()}
    assert checks["disclosure timestamp recorded"] is False
    assert LiveReadiness(cfg).ready is False


def test_disclosure_covers_the_hard_truths():
    text = " ".join(RISK_DISCLOSURE).lower()
    for word in ("lose", "leverage", "liquidation", "slippage", "outage",
                 "advisory only", "by hand"):
        assert word in text, "disclosure missing %r" % word


class StubLiveExchange:
    def market_order(self, pair, side, amount, price=None):
        return {"price": price or 100.0, "fee": 0.1}

    def attach_protection(self, *a, **k):
        return None

    def set_leverage(self, *a, **k):
        return None


def make_live_bot(tmp_path):
    cfg = BotConfig()
    cfg.mode = ModeConfig(dry_run=False, live_confirmation=live_conf())
    cfg.persistence.db_url = "sqlite:///%s/live.db" % tmp_path
    return AetherBot(cfg, RsiEmaCross(),
                     session=make_session(cfg.persistence.db_url),
                     live_exchange=StubLiveExchange(), once=True)


def test_live_engine_warns_on_every_order(tmp_path, caplog):
    bot = make_live_bot(tmp_path)
    assert bot.live is True
    with caplog.at_level(logging.WARNING):
        bot._open_trade("BTC/USDT", 100.0, 95.0, 50.0, 100.0, None)
    assert any("LIVE ORDER" in r.message and "REAL MONEY" in r.message
               for r in caplog.records)


def test_dry_run_engine_never_prints_real_money(tmp_path, caplog):
    cfg = BotConfig()
    cfg.persistence.db_url = "sqlite:///%s/dry.db" % tmp_path
    bot = AetherBot(cfg, RsiEmaCross(),
                    session=make_session(cfg.persistence.db_url), once=True)
    assert bot.live is False
    with caplog.at_level(logging.WARNING):
        bot._open_trade("BTC/USDT", 100.0, 95.0, 50.0, 100.0, None)
    assert not any("REAL MONEY" in r.message for r in caplog.records)
