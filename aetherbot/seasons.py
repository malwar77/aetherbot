"""Trading seasons: contiguous runs of trades, per mode.

A "season" is an honest unit of track record — a contiguous stretch of
trading in ONE mode (DEMO = dry-run paper, REAL MONEY = live). Seasons
are derived from the trades database; nothing is invented or smoothed.
A gap of more than GAP_DAYS days without a trade ends the season.

A REAL MONEY season can only ever appear here if a human has ALREADY
set mode.dry_run: false + live_confirmation in the YAML by hand —
this module records history. It never creates, switches, or approves
anything. DEMO seasons do not predict live performance.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List

GAP_DAYS = 7.0
LABELS = {"dry_run": "DEMO", "live": "REAL MONEY"}


def _parse_ts(ts: Any) -> datetime | None:
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def compute_seasons(trades: Iterable[Dict[str, Any]],
                    gap_days: float = GAP_DAYS) -> List[Dict[str, Any]]:
    """Group trades into per-mode seasons. trades need keys:
    mode, open_date, and optionally pnl, fee_paid, is_win."""
    rows = []
    for t in trades:
        dt = _parse_ts(t.get("open_date"))
        if dt is None or t.get("mode") not in LABELS:
            continue
        rows.append((dt, t))
    rows.sort(key=lambda x: x[0])

    seasons: List[Dict[str, Any]] = []
    for dt, t in rows:
        mode = t["mode"]
        pnl = _num(t.get("pnl"))
        fee = _num(t.get("fee_paid"))
        win = bool(t.get("is_win"))
        if (seasons and seasons[-1]["_mode"] == mode
                and (dt - seasons[-1]["_end"]).total_seconds()
                <= gap_days * 86400):
            s = seasons[-1]
            s["_end"] = dt
            s["trades"] += 1
            s["wins"] += 1 if win else 0
            s["net_pnl"] += pnl
            s["fees"] += fee
        else:
            seasons.append({"_mode": mode, "label": LABELS[mode],
                            "_start": dt, "_end": dt, "trades": 1,
                            "wins": 1 if win else 0,
                            "net_pnl": pnl, "fees": fee})
    out: List[Dict[str, Any]] = []
    for i, s in enumerate(seasons, 1):
        span = (s["_end"] - s["_start"]).total_seconds() / 86400
        out.append({
            "season": i, "mode": s["_mode"], "label": s["label"],
            "start": s["_start"].isoformat(),
            "end": s["_end"].isoformat(),
            "trades": s["trades"], "wins": s["wins"],
            "win_rate": round(100.0 * s["wins"] / s["trades"], 1),
            "net_pnl": round(s["net_pnl"], 2),
            "fees": round(s["fees"], 2),
            "span_days": round(span, 2),
        })
    return out


def load_seasons(db_url: str) -> List[Dict[str, Any]]:
    """Seasons from the trade DB. [] when the DB is missing/empty —
    never invented."""
    try:
        from .persistence.models import Trade, make_session
        session = make_session(db_url)
        trades = (session.query(Trade).order_by(Trade.open_date).all())
        out = compute_seasons([{
            "mode": t.mode, "open_date": t.open_date,
            "pnl": t.pnl, "fee_paid": t.fee_paid, "is_win": t.is_win,
        } for t in trades])
        session.close()
        return out
    except Exception:
        return []
