"""Season tests — hand-verified. Seasons are an honest track-record
unit derived from recorded trades; REAL MONEY seasons only exist
after a human flips dry_run by hand (never by code)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from aetherbot.seasons import compute_seasons, load_seasons


def _t(hours, mode="dry_run", pnl=1.0, fee=0.25, win=True):
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return {"mode": mode, "open_date": (t0 + timedelta(hours=hours)),
            "pnl": pnl, "fee_paid": fee, "is_win": win}


def test_empty():
    assert compute_seasons([]) == []


def test_contiguous_trades_one_season_with_stats():
    s = compute_seasons([_t(0), _t(2, pnl=-3.0, fee=0.5, win=False),
                         _t(5, pnl=2.0)])
    assert len(s) == 1
    assert s[0]["label"] == "DEMO"
    assert s[0]["trades"] == 3
    assert s[0]["wins"] == 2
    assert s[0]["win_rate"] == pytest.approx(66.7, abs=0.1)
    assert s[0]["net_pnl"] == pytest.approx(0.0, abs=1e-6)
    assert s[0]["fees"] == pytest.approx(1.0, abs=1e-6)


def test_gap_splits_seasons():
    s = compute_seasons([_t(0), _t(24 * 8)])  # 8-day gap
    assert len(s) == 2


def test_gap_under_seven_days_same_season():
    s = compute_seasons([_t(0), _t(24 * 7 - 1)])
    assert len(s) == 1


def test_mode_change_new_season():
    s = compute_seasons([_t(0), _t(1, mode="live")])
    assert len(s) == 2
    assert [x["label"] for x in s] == ["DEMO", "REAL MONEY"]


def test_unknown_mode_ignored():
    assert compute_seasons([_t(0, mode="weird")]) == []


def test_load_seasons_missing_db(tmp_path):
    assert load_seasons("sqlite:///" + str(tmp_path / "nope.db")) == []


def test_load_seasons_round_trip(tmp_path):
    from aetherbot.persistence.models import (Base, Trade, make_session)
    from sqlalchemy import create_engine
    url = "sqlite:///" + str(tmp_path / "t.db")
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    session = make_session(url)
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for i in range(4):
        session.add(Trade(pair="BTC/USDT", side="long", stake=25.0,
                          amount=0.001, entry_price=50000.0,
                          exit_price=50200.0, fee_paid=0.25,
                          pnl=1.75, roi=0.07, is_open=False,
                          is_win=True, stop_loss=45000.0,
                          stop_reason="manual_close",
                          strategy="ExampleStrategy", exchange="kraken",
                          mode="dry_run",
                          open_date=t0 + timedelta(hours=i),
                          close_date=t0 + timedelta(hours=i, minutes=30)))
    session.commit()
    session.close()
    seasons = load_seasons(url)
    assert len(seasons) == 1
    assert seasons[0]["label"] == "DEMO"
    assert seasons[0]["trades"] == 4
    assert seasons[0]["net_pnl"] == pytest.approx(7.0, abs=0.01)
