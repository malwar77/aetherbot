"""Morning status reporting — snapshot of the bot's state for the
Superagent ingest endpoint (morning WhatsApp report).

Facts only, from the config, the trades DB and the RiskManager:
- mode (dry_run|live) and start/current balance for dry-run
- open trades (pair, side, size, entry, stop)
- today's realized daily PnL (RiskManager semantics)
- daily-loss-limit usage (the daily halt distance)

Strictly read-only: it never places, approves or alters trades.
The RiskManager on this host stays authoritative.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

DEFAULT_POST_TIMEOUT_SECONDS = 10


def build_status(config, session, now: Optional[datetime] = None
                 ) -> Dict[str, Any]:
    """Build the status payload dict for one config/DB."""
    from .persistence.models import Trade
    from .risk.risk_manager import RiskManager
    now = now or datetime.now(timezone.utc)

    live = False
    try:
        live = config.effective_live_mode()
    except Exception:                     # noqa: BLE001 — report anyway
        live = False

    open_trades = (session.query(Trade)
                   .filter(Trade.is_open.is_(True))
                   .all())

    risk = RiskManager(config, session)
    daily_pnl = float(risk.daily_pnl(now))
    limit_pct = float(config.risk.daily_loss_limit_pct)
    start_balance = float(config.account.dry_run_start_balance)
    # daily-loss-limit usage: fraction of the limit consumed today
    # (positive losses consume; profits reset it to zero)
    if start_balance > 0:
        # % of start balance lost today (0 when in profit)
        loss_used_pct = round(max(0.0, -daily_pnl)
                              / start_balance * 100.0, 2)
    else:
        loss_used_pct = 0.0

    return {
        "project": "aetherbot",
        "account": config.exchange.name,
        "mode": "live" if live else "dry_run",
        "generated_at": now.isoformat(),
        "balance": start_balance if not live else None,
        "daily_pnl": daily_pnl,
        "open_positions": [
            {"symbol": t.pair, "side": t.side, "size": t.amount,
             "entry": t.entry_price, "stop": t.stop_loss,
             "unrealized_pnl": None}
            for t in open_trades
        ],
        "kill_switch": {
            "daily_used_pct": loss_used_pct,
            "weekly_used_pct": None,
            "blocked": loss_used_pct >= limit_pct,
        },
        "host": os.environ.get("HOSTNAME") or os.uname().nodename,
        "notes": "daily loss limit %.2f%% of start balance %.2f" % (
            limit_pct, start_balance),
    }


def post_status(payload: Dict[str, Any], post_url: str, token: str,
                timeout: int = DEFAULT_POST_TIMEOUT_SECONDS
                ) -> Tuple[bool, int, str]:
    """POST the status payload. Returns (ok, http_status, body)."""
    body = json.dumps(dict(payload, token=token)).encode("utf-8")
    req = urllib.request.Request(
        post_url, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (200 <= resp.status < 300, resp.status,
                    resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return (False, e.code, e.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError) as e:
        return (False, 0, str(e))
