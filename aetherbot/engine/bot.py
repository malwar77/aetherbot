"""Core engine — the live/dry-run trading loop.

Execution order per pair, per cycle (all gates in code, none in prompts):
1. fetch CLOSED candles (never the forming candle — no lookahead)
2. strategy populate -> signal from the last closed candle
3. propose stake (fixed/percentage/risk_pct) + stop via RiskManager
4. AI brain annotation (advisory; "against" can ONLY skip, never create)
5. RiskManager.check_entry veto — final authority before any order
6. execute via exchange (live) or DryRunExchange (paper)
7. manage open trades each cycle: local stop monitoring, ROI table,
   trailing stop, strategy exit signals

Live mode additionally passes config.effective_live_mode() at startup —
the bot refuses to start live without the human opt-ins.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import pandas as pd

from ..ai.brain import AIBrain
from ..config import BotConfig
from ..data.data import CandleFeed
from ..exchange.dry_run import DryRunExchange
from ..persistence.models import Trade, make_session, utcnow
from ..risk.position_sizing import stake_fixed, stake_percentage, stake_risk_pct
from ..risk.risk_manager import RiskManager

log = logging.getLogger("aetherbot.engine")


class AetherBot:
    def __init__(self, config: BotConfig, strategy, session=None,
                 live_exchange=None, brain: AIBrain | None = None,
                 feed: CandleFeed | None = None, once: bool = False):
        self.cfg = config
        self.strategy = strategy
        self.session = session or make_session(config.persistence.db_url)
        self.once = once
        self.paused = False
        self.live = False
        self._started_at = None

        # THE MODE GATE — refuses live without human opt-ins
        self.live = config.effective_live_mode()
        self.mode_name = "live" if self.live else "dry_run"

        self.brain = brain or (AIBrain(config.ai.ollama_url,
                                       config.ai.ollama_model)
                               if config.ai.enabled else None)

        if self.live:
            if live_exchange is None:
                from ..exchange.exchange import CcxtExchange
                live_exchange = CcxtExchange(config.exchange)
            self.exchange = live_exchange
        else:
            self.exchange = DryRunExchange(
                start_balance=config.account.dry_run_start_balance)
        self.feed = feed or CandleFeed(
            source=config.trading.candle_source,
            exchange=None if self.live else None, pairs=[])
        self.risk = RiskManager(config, self.session,
                                balance_provider=self._balance)

        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ---------------- helpers ----------------

    def _balance(self) -> float:
        if self.live:
            try:
                b = self.exchange.fetch_balance()
                return float(b.get("total", {}).get(
                    self.cfg.trading.stake_currency, 0.0))
            except Exception as exc:  # noqa: BLE001
                log.error("balance fetch failed: %s", exc)
                return 0.0
        return self.exchange.balance

    def open_trades(self) -> list[Trade]:
        return self.session.query(Trade).filter(Trade.is_open.is_(True)).all()

    # ---------------- main loop ----------------

    def run(self, poll_seconds: float | None = None):
        self._started_at = utcnow()
        log.warning("AetherBot starting in %s MODE %s",
                    self.mode_name.upper(),
                    "(REAL MONEY)" if self.live else "(paper — no real "
                    "orders)")
        tf = self.cfg.trading.timeframe
        poll = poll_seconds or 30
        while not self._stop.is_set():
            try:
                self.cycle()
            except Exception as exc:  # noqa: BLE001 — keep the bot alive
                log.exception("cycle failed: %s", exc)
            if self.once:
                break
            time.sleep(poll)
        log.info("engine stopped")

    def stop(self):
        self._stop.set()

    def cycle(self):
        if self.paused:
            return
        now = utcnow()
        self._manage_open_trades(now)
        self._check_entries(now)

    # ---------------- entries ----------------

    def _check_entries(self, now: datetime):
        if self.paused:
            return
        for pair in self.cfg.tradeable_pairs():
            if any(t.pair == pair for t in self.open_trades()):
                continue
            try:
                df = self.feed.ohlcv(pair, self.cfg.trading.timeframe,
                                     limit=200)
            except Exception as exc:  # noqa: BLE001
                log.warning("no data for %s: %s", pair, exc)
                continue
            df = CandleFeed.closed(df)
            advised = self.strategy.advice(df)
            if len(advised) == 0:
                continue
            last = advised.iloc[-1]
            if not bool(last.get("enter_long", False)):
                continue
            entry = float(last["close"])
            stop = self.risk.initial_stop(entry)
            stake = self._propose_stake(entry, stop)
            balance = self._balance()
            proposal = self._proposal(pair, entry, stake, last)
            annotation = None
            if self.brain is not None:
                annotation = self.brain.annotate(proposal)
                proposal["llm_annotation"] = annotation
                if (annotation["agreement"] == "against"
                        and self.cfg.ai.advisory_veto):
                    log.info("[ai-veto] %s: LLM 'against' — signal skipped "
                             "(advisory veto)", pair)
                    continue
            veto = self.risk.check_entry(pair, stake, entry, stop,
                                        len(self.open_trades()), balance,
                                        now=now)
            if not veto.allowed:
                log.info("entry vetoed for %s: %s", pair, veto.reasons)
                continue
            self._open_trade(pair, entry, stop, stake, last, annotation)

    def _propose_stake(self, entry: float, stop: float) -> float:
        s = self.cfg.trading.stake
        balance = self._balance()
        if s.mode.value == "fixed":
            stake = stake_fixed(s.amount)
        elif s.mode.value == "risk_pct":
            r = stake_risk_pct(balance, s.amount, entry, stop)
            stake = r.stake
        else:
            stake = stake_percentage(balance, s.amount)
        return self.strategy.custom_stake_amount("", balance, stake)

    def _proposal(self, pair, entry, stake, last) -> dict:
        ind = {}
        for col in ("rsi", "ema_fast", "ema_slow", "atr", "ml_predict"):
            if col in last.index and pd.notna(last[col]):
                v = last[col]
                ind[col] = float(v) if hasattr(v, "item") else v
        return {
            "pair": pair, "side": "long", "entry_price": entry,
            "stake": stake, "strategy": type(self.strategy).__name__,
            "timeframe": self.cfg.trading.timeframe,
            "indicators": ind,
            "reasons": "strategy enter_long signal on last closed candle",
        }

    def _open_trade(self, pair, entry, stop, stake, last, annotation):
        amount = stake / entry
        if self.live:
            lev = int(self.strategy.leverage(pair, utcnow(), entry, 1.0))
            if self.cfg.exchange.market_type == "future" and lev > 1:
                self.exchange.set_leverage(pair, lev)
        if self.live:
            order = self.exchange.market_order(pair, "buy", amount)
        else:
            # dry-run simulator fills at the last closed candle price
            order = self.exchange.market_order(pair, "buy", amount,
                                               price=entry)
        fill = float(order.get("price") or entry)
        fee = float(order.get("fee", 0.0))
        trade = Trade(pair=pair, side="long", stake=stake, amount=amount,
                     entry_price=fill, stop_loss=stop, fee_paid=fee,
                     strategy=type(self.strategy).__name__,
                     exchange=self.cfg.exchange.name,
                     mode=self.mode_name, is_open=True,
                     llm_annotation=annotation)
        self.session.add(trade)
        self.session.commit()
        # exchange-side protection where supported; local monitoring always
        self.exchange.attach_protection(pair, amount, stop, None)
        log.info("opened %s %s trade: stake %.2f @ %.8g stop %.8g [%s]",
                 pair, self.mode_name, stake, fill, stop,
                 (annotation or {}).get("agreement", "no-ai"))

    # ---------------- trade management ----------------

    def _manage_open_trades(self, now: datetime):
        for trade in self.open_trades():
            try:
                price = float(self.feed.ohlcv(
                    trade.pair, self.cfg.trading.timeframe,
                    limit=1).iloc[-1]["close"])
            except Exception:  # noqa: BLE001
                continue
            # trailing stop update
            new_stop = self.risk.update_trailing_stop(trade, price)
            if new_stop:
                trade.stop_loss = new_stop
            # strategy custom stop override
            custom = self.strategy.custom_stoploss(
                trade.pair, trade, now, price, self._profit(trade, price))
            if custom is not None and custom < 0:
                candidate = trade.entry_price * (1 + custom)
                if trade.side == "long" and candidate > (trade.stop_loss or 0):
                    trade.stop_loss = candidate
            # local stop monitoring (ALWAYS on, even with exchange stops)
            if trade.stop_loss and price <= trade.stop_loss:
                self._close_trade(trade, price, "stoploss")
                continue
            # ROI table
            # SQLite returns naive datetimes; compare in naive UTC
            minutes = (now.replace(tzinfo=None)
                       - trade.open_date.replace(tzinfo=None)
                       ).total_seconds() / 60
            roi_at = self.risk.roi_target(trade.entry_price, minutes,
                                          self.strategy.minimal_roi,
                                          trade.side)
            if roi_at and price >= roi_at:
                self._close_trade(trade, price, "roi")
                continue
            # strategy exit signal from the last closed candle
            df = self.feed.ohlcv(trade.pair, self.cfg.trading.timeframe,
                                 limit=60)
            advised = self.strategy.advice(CandleFeed.closed(df))
            if len(advised) and bool(advised.iloc[-1].get("exit_long",
                                                          False)):
                self._close_trade(trade, price, "exit_signal")
            self.session.commit()

    @staticmethod
    def _profit(trade: Trade, price: float) -> float:
        return (price - trade.entry_price) / trade.entry_price

    def _close_trade(self, trade: Trade, price: float, reason: str):
        if self.live:
            self.exchange.close_position(trade.pair, trade.amount, "long")
        else:
            self.exchange.close_position(trade.pair, trade.amount, "long",
                                         price=price)
        pnl = (price - trade.entry_price) * trade.amount - trade.fee_paid
        trade.exit_price = price
        trade.close_date = utcnow()
        trade.pnl = pnl
        trade.roi = pnl / trade.stake if trade.stake else 0.0
        trade.is_open = False
        trade.is_win = pnl > 0
        trade.stop_reason = reason
        self.session.commit()
        self.risk.record_loss(trade)
        log.info("closed %s: pnl %.4f (%s)", trade.pair, pnl, reason)

    # ---------------- status ----------------

    def status(self) -> dict:
        closed = self.session.query(Trade).filter(
            Trade.is_open.is_(False)).all()
        wins = [t for t in closed if t.is_win]
        return {
            "mode": self.mode_name,
            "paused": self.paused,
            "strategy": type(self.strategy).__name__,
            "timeframe": self.cfg.trading.timeframe,
            "pairs": self.cfg.tradeable_pairs(),
            "open_trades": [
                {"id": t.id, "pair": t.pair, "entry": t.entry_price,
                 "amount": t.amount, "stop": t.stop_loss,
                 "open_date": str(t.open_date),
                 "unrealized": self._profit(t, t.entry_price)}
                for t in self.open_trades()],
            "closed_trades": len(closed),
            "wins": len(wins),
            "total_pnl": sum(t.pnl or 0 for t in closed),
            "balance": self._balance(),
            "daily_pnl": self.risk.daily_pnl(utcnow()),
            "consecutive_losses": self.risk.consecutive_losses(),
        }
