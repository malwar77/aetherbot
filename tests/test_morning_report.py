"""Tests: morning status beacon (build_status + post_status).

Hand-verified expectations. Reporting is strictly read-only: it never
places, approves or alters trades; these tests pin the payload shape
against the ingest contract too."""
import io
import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aetherbot.morning_report as morning_report
from aetherbot.config import BotConfig, LiveConfirmation, ModeConfig
from aetherbot.morning_report import (_pick_conversation_id,
                                      build_status, send_beacon)
from aetherbot.persistence.models import Trade, make_session

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_db(tmp_path):
    cfg = BotConfig()
    cfg.persistence.db_url = "sqlite:///%s/r.db" % tmp_path
    session = make_session(cfg.persistence.db_url)
    return cfg, session


def add_trade(session, pair="BTC/USDT", side="long", is_open=True,
              pnl=None, close_at=None, entry=100.0, stop=95.0,
              amount=0.5):
    t = Trade(pair=pair, side=side, stake=50.0, amount=amount,
              entry_price=entry, exit_price=None if is_open else 101.0,
              fee_paid=0.0, pnl=pnl, roi=None, is_open=is_open,
              is_win=None, stop_loss=stop, stop_reason=None,
              strategy="Test", exchange="binance",
              mode="dry_run", leverage=1.0,
              open_date=NOW - timedelta(hours=2),
              close_date=close_at)
    session.add(t)
    session.commit()
    return t


def test_build_status_dry_run_defaults(tmp_path):
    cfg, session = make_db(tmp_path)
    s = build_status(cfg, session, now=NOW)
    assert s["project"] == "aetherbot"
    assert s["mode"] == "dry_run"
    assert s["account"] == "binance"
    assert s["balance"] == 10000.0      # dry-run start balance
    assert s["daily_pnl"] == 0.0        # nothing closed today
    assert s["open_positions"] == []
    assert s["kill_switch"]["blocked"] is False
    assert s["generated_at"] == NOW.isoformat()
    session.close()


def test_build_status_math_hand_verified(tmp_path):
    cfg, session = make_db(tmp_path)
    # one open trade + one closed trade today with pnl -150
    add_trade(session, pair="ETH/USDT", is_open=True, entry=2000.0,
              stop=1900.0, amount=0.25)
    add_trade(session, pair="BTC/USDT", is_open=False, pnl=-150.0,
              close_at=NOW - timedelta(hours=1))
    s = build_status(cfg, session, now=NOW)
    assert s["daily_pnl"] == -150.0
    # loss used = 150/10000 start balance = 1.5% (hand-verified)
    assert s["kill_switch"]["daily_used_pct"] == 1.5
    assert s["kill_switch"]["blocked"] is False  # limit is 5%
    # open position shape
    assert len(s["open_positions"]) == 1
    p = s["open_positions"][0]
    assert p["symbol"] == "ETH/USDT" and p["side"] == "long"
    assert p["entry"] == 2000.0 and p["stop"] == 1900.0
    assert p["size"] == 0.25
    session.close()


def test_build_status_daily_limit_block_flagged(tmp_path):
    cfg, session = make_db(tmp_path)
    # -600 = 6% of 10000 -> above the 5% daily loss limit
    add_trade(session, is_open=False, pnl=-600.0,
              close_at=NOW - timedelta(hours=1))
    s = build_status(cfg, session, now=NOW)
    assert s["kill_switch"]["daily_used_pct"] == 6.0
    assert s["kill_switch"]["blocked"] is True
    session.close()


def test_build_status_profit_clamps_used_to_zero(tmp_path):
    cfg, session = make_db(tmp_path)
    add_trade(session, is_open=False, pnl=250.0,
              close_at=NOW - timedelta(hours=1))
    s = build_status(cfg, session, now=NOW)
    assert s["daily_pnl"] == 250.0
    assert s["kill_switch"]["daily_used_pct"] == 0.0
    session.close()


def test_build_status_live_mode_when_opted_in(tmp_path):
    cfg, session = make_db(tmp_path)
    cfg.mode = ModeConfig(
        dry_run=False,
        live_confirmation=LiveConfirmation(
            confirmed_live=True, risk_disclosure_accepted=True,
            risk_disclosure_accepted_at=NOW))
    s = build_status(cfg, session, now=NOW)
    assert s["mode"] == "live"
    # balance is None for live: a read-only report does not query the
    # real exchange — live balance lives on the host
    assert s["balance"] is None
    session.close()


def test_pick_conversation_id_shapes():
    assert _pick_conversation_id(
        [{"id": "a"}, {"id": "b"}]) == "a"
    assert _pick_conversation_id(
        [{"id": "a"}, {"id": "b", "is_default": True}]) == "b"
    assert _pick_conversation_id(
        {"conversations": [{"id": "c"}]}) == "c"
    assert _pick_conversation_id({"id": "e"}) == "e"
    assert _pick_conversation_id({}) is None
    assert _pick_conversation_id([]) is None


def test_send_beacon_round_trip(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.method, req.full_url, req.data,
                      req.get_header("Api_key")))
        if req.full_url.endswith("/conversations"):
            return FakeResponse(200, json.dumps(
                [{"id": "conv-1", "is_default": True},
                 {"id": "conv-2"}]))
        return FakeResponse(200, '{"message": {"content": "stored"}}')

    monkeypatch.setattr(morning_report.urllib.request, "urlopen",
                        fake_urlopen)
    payload = {"project": "aetherbot", "account": "binance",
               "mode": "dry_run", "generated_at": NOW.isoformat()}
    ok, detail = send_beacon(payload,
                             "https://host/api/agents/A1", "key-1")
    assert ok is True
    m1, u1, d1, k1 = calls[0]
    assert m1 == "GET" and u1 == "https://host/api/agents/A1/conversations"
    assert k1 == "key-1" and d1 is None
    m2, u2, d2, k2 = calls[1]
    assert m2 == "POST"
    assert u2 == "https://host/api/agents/A1/conversations/conv-1/messages"
    assert k2 == "key-1"
    sent = json.loads(d2.decode())
    assert sent["message"].startswith("STATUS BEACON ")
    inner = json.loads(sent["message"][len("STATUS BEACON "):])
    assert inner["project"] == "aetherbot"
    assert inner["account"] == "binance"


def test_send_beacon_conversation_fetch_fails(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"message": "bad key"}'))
    monkeypatch.setattr(morning_report.urllib.request, "urlopen",
                        fake_urlopen)
    ok, detail = send_beacon({"x": 1}, "https://host/api/agents/A1", "bad")
    assert ok is False and "401" in detail


def test_send_beacon_no_conversation_found(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return FakeResponse(200, json.dumps({"unexpected": 1}))
    monkeypatch.setattr(morning_report.urllib.request, "urlopen",
                        fake_urlopen)
    ok, detail = send_beacon({"x": 1}, "https://host/api/agents/A1", "k")
    assert ok is False and "no conversation" in detail


def test_send_beacon_post_fails(monkeypatch):
    state = {"n": 0}

    def fake_urlopen(req, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            return FakeResponse(200, json.dumps([{"id": "c1"}]))
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"message": "schema mismatch"}'))
    monkeypatch.setattr(morning_report.urllib.request, "urlopen",
                        fake_urlopen)
    ok, detail = send_beacon({"x": 1}, "https://host/api/agents/A1", "k")
    assert ok is False and "400" in detail and "schema mismatch" in detail
