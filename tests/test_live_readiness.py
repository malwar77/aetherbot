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
    cfg.persistence.db_url = _seed_paper_history(30, days=31)
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


def _seed_paper_history(n_trades: int, days: float) -> str:
    """Temporary SQLite DB with n_trades dry-run trades spanning
    `days` days. Returns its db_url."""
    import os
    import tempfile
    from datetime import datetime, timedelta, timezone
    from aetherbot.persistence.models import (Base, Trade, make_session)
    from sqlalchemy import create_engine
    path = os.path.join(tempfile.mkdtemp(), "paper.db")
    url = "sqlite:///" + path
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    session = make_session(url)
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(n_trades):
        session.add(Trade(pair="BTC/USDT", side="long", stake=25.0,
                          amount=0.001, entry_price=50000.0,
                          exit_price=50100.0, fee_paid=0.25,
                          pnl=1.0, roi=0.04, is_open=False, is_win=True,
                          stop_loss=45000.0, stop_reason="manual_close",
                          strategy="ExampleStrategy", exchange="kraken",
                          mode="dry_run",
                          open_date=t0 + timedelta(
                              days=days * i / max(1, n_trades - 1)),
                          close_date=t0 + timedelta(hours=2)))
    session.commit()
    session.close()
    return url


def test_live_gate_requires_paper_track_record():
    """No paper history -> the audit refuses the live gate, even when
    every human-only YAML field is correctly set."""
    cfg = BotConfig()
    cfg.mode = ModeConfig(dry_run=False, live_confirmation=live_conf())
    cfg.trading.pairs.whitelist = ["BTC/USDT"]
    cfg.trading.pairs.blacklist = []
    # empty DB
    cfg.persistence.db_url = _seed_paper_history(0, 0)
    lr = LiveReadiness(cfg)
    checks = {n: ok for n, ok, _ in lr.checks()}
    assert checks["paper track record (>= 30 trades over >= 30 days)"] is False

    # too few trades / too short a span still fails
    cfg.persistence.db_url = _seed_paper_history(5, days=3)
    lr = LiveReadiness(cfg)
    checks = {n: ok for n, ok, _ in lr.checks()}
    assert checks["paper track record (>= 30 trades over >= 30 days)"] is False

    # 30+ trades over 30+ days passes
    cfg.persistence.db_url = _seed_paper_history(30, days=31)
    lr = LiveReadiness(cfg)
    checks = {n: ok for n, ok, _ in lr.checks()}
    assert checks["paper track record (>= 30 trades over >= 30 days)"] is True
