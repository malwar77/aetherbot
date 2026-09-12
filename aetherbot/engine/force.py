"""Force-enter / force-exit — still gated by the RiskManager.

Force commands skip the strategy signal, but NEVER the risk gates:
force_enter passes the same check_entry veto as signalled entries
(including the AI advisory veto when enabled).
"""
from __future__ import annotations

import logging
from datetime import timezone

from ..persistence.models import utcnow
from ..risk.position_sizing import stake_percentage

log = logging.getLogger("aetherbot.force")


def force_enter(bot, pair: str) -> str:
    if any(t.pair == pair for t in bot.open_trades()):
        return "already have an open trade on %s" % pair
    try:
        df = bot.feed.ohlcv(pair, bot.cfg.trading.timeframe, limit=1)
        entry = float(df.iloc[-1]["close"])
    except Exception as exc:  # noqa: BLE001
        return "no market data for %s: %s" % (pair, exc)
    stop = bot.risk.initial_stop(entry)
    stake = bot._propose_stake(entry, stop)
    proposal = bot._proposal(pair, entry, stake,
                             df.iloc[-1])
    if bot.brain is not None:
        annotation = bot.brain.annotate(proposal)
        proposal["llm_annotation"] = annotation
        if (annotation["agreement"] == "against"
                and bot.cfg.ai.advisory_veto):
            return "forced entry vetoed by AI advisory 'against' (%s)" \
                % annotation["summary"][:200]
    veto = bot.risk.check_entry(pair, stake, entry, stop,
                                len(bot.open_trades()), bot._balance(),
                                now=utcnow())
    if not veto.allowed:
        return "forced entry REFUSED by risk gates: %s" % veto.reasons
    bot._open_trade(pair, entry, stop, stake, df.iloc[-1],
                    proposal.get("llm_annotation"))
    return "forced entry executed on %s (%s mode) — passed all risk gates" \
        % (pair, bot.mode_name)


def force_exit(bot, trade_id: str) -> str:
    try:
        tid = int(trade_id)
    except ValueError:
        return "trade id must be an integer"
    trade = next((t for t in bot.open_trades() if t.id == tid), None)
    if trade is None:
        return "no open trade with id %s" % trade_id
    try:
        price = float(bot.feed.ohlcv(trade.pair,
                                     bot.cfg.trading.timeframe,
                                     limit=1).iloc[-1]["close"])
    except Exception as exc:  # noqa: BLE001
        return "no market data for %s: %s" % (trade.pair, exc)
    bot._close_trade(trade, price, "force_exit")
    return "closed trade #%d on %s" % (tid, trade.pair)
