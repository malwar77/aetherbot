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

BEACON_TIMEOUT_SECONDS = 120


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


def _pick_conversation_id(convs):
    """Best-effort conversation picker. Handles a bare list, a wrapped
    dict ({conversations|data|items|results: [...]}) and a single
    conversation object. Prefers the default conversation."""
    items = None
    if isinstance(convs, list):
        items = convs
    elif isinstance(convs, dict):
        for key in ("conversations", "data", "items", "results"):
            if isinstance(convs.get(key), list):
                items = convs[key]
                break
        if items is None and isinstance(convs.get("id"), str):
            return convs["id"]
    if not items:
        return None
    for c in items:
        if isinstance(c, dict) and (c.get("is_default")
                                    or c.get("default")):
            return c.get("id")
    first = items[0]
    return first.get("id") if isinstance(first, dict) else None


def send_beacon(payload, api_base, api_key,
               timeout=BEACON_TIMEOUT_SECONDS):
    """Send the status payload to the agent via the external Agent API
    as a STATUS BEACON message. api_base is the agent's API root,
    e.g. https://<host>/api/agents/<agent_id>. The agent parses and
    stores the beacon; this side never waits for trading decisions.
    Returns (ok, detail)."""
    base = api_base.rstrip("/")
    req = urllib.request.Request(
        base + "/conversations", method="GET",
        headers={"api_key": api_key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            convs = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return (False, "conversation fetch failed: HTTP %s %s"
                % (e.code, e.read().decode("utf-8", "replace")[:200]))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, "conversation fetch failed: %s" % e
    conv_id = _pick_conversation_id(convs)
    if not conv_id:
        return False, ("no conversation found in API response: %s"
                       % json.dumps(convs)[:200])
    body = json.dumps({
        "message": "STATUS BEACON " + json.dumps(payload),
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/conversations/%s/messages" % conv_id, data=body,
        method="POST",
        headers={"api_key": api_key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8", "replace")[:200]
            return True, resp_body
    except urllib.error.HTTPError as e:
        return (False, "beacon POST failed: HTTP %s %s"
                % (e.code, e.read().decode("utf-8", "replace")[:200]))
    except (urllib.error.URLError, OSError) as e:
        return False, "beacon POST failed: %s" % e
