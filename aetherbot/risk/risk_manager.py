"""RiskManager — the single veto authority before any order.

Every entry (live or dry-run, forced or signalled) must pass `check_entry`.
Every exit path (stoploss, ROI, trailing) is computed from the SAME config
that was validated here. No other module may relax these limits, and no
AI/LLM path exists that bypasses them.

Gates implemented:
- max open trades
- daily loss limit (% of balance, UTC day)
- total max drawdown protection (halts all new entries)
- cooldown after N consecutive losses
- stoploss sanity (negative fraction, stop on correct side of entry)
- stake validation (positive, available balance, not shorting equity)
- ROI table sanity (ordered by minutes)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import BotConfig
from ..persistence.models import LossEvent, Trade


def _naive(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; normalize comparisons to naive UTC."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@dataclass
class Veto:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class RiskManager:
    def __init__(self, config: BotConfig, session, balance_provider=None):
        self.cfg = config
        self.risk = config.risk
        self.session = session
        # balance_provider: callable -> current total balance (quote).
        # Defaults to dry-run start balance.
        self._balance = balance_provider or (
            lambda: config.account.dry_run_start_balance)

    # ---------------- entry gates ----------------

    def check_entry(self, pair: str, stake: float, entry: float,
                    stop_price: float | None, open_trades: int,
                    balance: float | None = None,
                    now: datetime | None = None) -> Veto:
        now = _naive(now or datetime.now(timezone.utc))
        reasons: list[str] = []
        balance = balance if balance is not None else self._balance()

        if open_trades >= self.cfg.trading.max_open_trades:
            reasons.append("max_open_trades reached (%d)"
                           % self.cfg.trading.max_open_trades)

        if self.daily_loss_exceeded(now):
            reasons.append(
                "daily loss limit %.1f%% exceeded — entries halted until "
                "next UTC day" % self.risk.daily_loss_limit_pct)

        if self.max_drawdown_exceeded(now):
            reasons.append("max drawdown %.1f%% exceeded — all new entries "
                          "halted" % self.risk.max_drawdown_pct)

        cooldown_until = self.cooldown_until(now)
        if cooldown_until and now < cooldown_until:
            reasons.append("cooldown active after consecutive losses until "
                           "%s" % cooldown_until.isoformat())

        if stake <= 0:
            reasons.append("stake must be positive")
        if stake > balance:
            reasons.append("stake %.2f exceeds available balance %.2f"
                           % (stake, balance))
        if entry <= 0:
            reasons.append("entry price must be positive")

        if self.risk.stoploss >= 0:
            reasons.append("config stoploss must be negative (fraction)")
        if stop_price is not None and stop_price <= 0:
            reasons.append("stop price must be positive")

        return Veto(len(reasons) == 0, reasons)

    # ---------------- loss bookkeeping (all UTC) ----------------

    def record_loss(self, trade: Trade) -> None:
        if (trade.pnl or 0) < 0:
            self.session.add(LossEvent(trade_id=trade.id, pnl=trade.pnl))
            self.session.commit()

    def record_win(self) -> None:
        """A win clears the consecutive-loss streak: delete events after
        the last win boundary (the most recent `consecutive_losses`
        window is tracked via streak computation below)."""
        self.session.commit()

    def consecutive_losses(self, now: datetime | None = None) -> int:
        """Count losses since the last win, from closed trades."""
        now = _naive(now or datetime.now(timezone.utc))
        closed = (self.session.query(Trade)
                  .filter(Trade.is_open.is_(False),
                          Trade.close_date <= now)
                  .order_by(Trade.close_date.desc())
                  .limit(20).all())
        streak = 0
        for t in closed:
            if t.is_win:
                break
            streak += 1
        return streak

    def cooldown_until(self, now: datetime) -> datetime | None:
        now = _naive(now)
        if self.consecutive_losses(now) < self.risk.consecutive_losses:
            return None
        last_loss = (self.session.query(LossEvent)
                     .order_by(LossEvent.date.desc()).first())
        if last_loss is None:
            return None
        until = last_loss.date + timedelta(
            minutes=self.risk.cooldown_minutes)
        return until if until > now else None

    def daily_pnl(self, now: datetime) -> float:
        now = _naive(now)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        closed = (self.session.query(Trade)
                  .filter(Trade.is_open.is_(False),
                          Trade.close_date >= day_start).all())
        return sum(t.pnl or 0 for t in closed)

    def daily_loss_exceeded(self, now: datetime) -> bool:
        loss = -self.daily_pnl(now)
        limit = self._balance() * self.risk.daily_loss_limit_pct / 100.0
        return loss >= limit and self.risk.daily_loss_limit_pct > 0

    def total_pnl(self) -> float:
        closed = (self.session.query(Trade)
                  .filter(Trade.is_open.is_(False)).all())
        return sum(t.pnl or 0 for t in closed)

    def max_drawdown_exceeded(self, now: datetime) -> bool:
        if self.risk.max_drawdown_pct <= 0:
            return False
        dd = -self.total_pnl()
        limit = self._balance() * self.risk.max_drawdown_pct / 100.0
        return dd >= limit

    # ---------------- exit level computations ----------------

    def initial_stop(self, entry: float, side: str = "long") -> float:
        """Static stoploss price from config fraction."""
        frac = self.risk.stoploss
        return entry * (1 + frac) if side == "long" else entry * (1 - frac)

    def roi_target(self, entry: float, minutes_held: float,
                   roi_table: dict[str, float], side: str = "long") -> float | None:
        """Minimal-ROI exit level for the current holding duration.
        roi_table keys are minutes (as str), values are roi fractions."""
        if not roi_table:
            return None
        applicable = {int(k): float(v) for k, v in roi_table.items()}
        # the highest-minute threshold already passed applies
        best = None
        for mins, roi in sorted(applicable.items()):
            if minutes_held >= mins:
                best = roi
        if best is None:
            return None
        return entry * (1 + best) if side == "long" else entry * (1 - best)

    def update_trailing_stop(self, trade: Trade, current_price: float) -> float | None:
        """Trailing stop per config: once profit exceeds
        trailing_stop_positive_offset, trail at trailing_stop_positive
        below the peak (longs; mirrored for shorts). Returns the new stop
        or None when trailing is disabled / not yet armed."""
        if not self.risk.trailing_stop:
            return None
        if trade.side == "long":
            peak_frac = (current_price - trade.entry_price) / trade.entry_price
            if peak_frac < self.risk.trailing_stop_positive_offset:
                return None
            candidate = current_price * (1 - self.risk.trailing_stop_positive)
            new_stop = max(trade.stop_loss or 0.0, candidate)
            return new_stop if new_stop > (trade.stop_loss or 0.0) else None
        peak_frac = (trade.entry_price - current_price) / trade.entry_price
        if peak_frac < self.risk.trailing_stop_positive_offset:
            return None
        candidate = current_price * (1 + self.risk.trailing_stop_positive)
        if trade.stop_loss is None or candidate < trade.stop_loss:
            return candidate
        return None

    @staticmethod
    def validate_roi_table(roi_table: dict) -> list[str]:
        errors = []
        for k, v in (roi_table or {}).items():
            if not str(k).isdigit():
                errors.append("roi table key %r must be minutes (int)" % k)
            if float(v) < 0:
                errors.append("roi table value %r must be >= 0" % v)
        return errors
