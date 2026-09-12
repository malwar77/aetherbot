"""Simple event-driven backtester — dry-run of the past.

Applies the same strategy signals, stoploss, trailing stop and ROI table
as the live engine, over downloaded OHLCV. Costs: configurable fee bps
per side. Also reports buy-and-hold and EMA-crossover benchmarks — a
strategy that cannot beat naive benchmarks after costs is surfaced
loudly, not hidden.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from ..ta import ema

log = logging.getLogger("aetherbot.backtest")


@dataclass
class BacktestResult:
    trades: list[dict] = field(default_factory=list)
    total_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    max_drawdown: float = 0.0
    buy_hold_return: float = 0.0
    beats_buy_hold: bool = False
    beats_ema_cross: bool = False

    def summary(self) -> str:
        n = len(self.trades)
        wr = (self.wins / n * 100) if n else 0.0
        verdict = ("STRATEGY BEATS both benchmarks after costs"
                   if self.beats_buy_hold and self.beats_ema_cross
                   else "STRATEGY DOES NOT BEAT benchmarks after costs — "
                        "treat accordingly")
        return ("backtest: %d trades, %.1f%% win rate, total pnl %.4f, "
                "max drawdown %.2f%% | buy&hold %.2f%% — %s"
                % (n, wr, self.total_pnl, self.max_drawdown * 100,
                   self.buy_hold_return * 100, verdict))


def backtest(config, strategy, df: pd.DataFrame, pair: str = "PAIR",
             fee_rate: float = 0.001,
             start_balance: float = 10000.0) -> BacktestResult:
    risk = config.risk
    advised = strategy.advice(df)
    res = BacktestResult()
    balance = start_balance
    peak = balance
    stake_mode = config.trading.stake.mode.value
    stake_amt = config.trading.stake.amount
    max_open = config.trading.max_open_trades

    open_trades: list[dict] = []
    for i in range(len(advised)):
        row = advised.iloc[i]
        price = float(row["close"])
        # manage open trades on this candle (stop, trailing, roi, exit)
        for t in list(open_trades):
            new_stop = _trailing(t, price, risk)
            if new_stop:
                t["stop"] = max(t["stop"], new_stop)
            hit = (price <= t["stop"])
            roi_at = _roi(t, i, strategy)
            roi_hit = roi_at and price >= roi_at
            exit_sig = bool(row.get("exit_long", False))
            if hit or roi_hit or exit_sig:
                reason = ("stoploss" if hit
                          else "roi" if roi_hit else "exit_signal")
                _close(res, t, price, reason, fee_rate)
                open_trades.remove(t)
        # entries
        if bool(row.get("enter_long", False)) and len(open_trades) < max_open:
            stop = price * (1 + risk.stoploss)
            if stake_mode == "fixed":
                stake = min(stake_amt, balance)
            elif stake_mode == "risk_pct":
                risk_amt = balance * stake_amt / 100.0
                stake = min(risk_amt / max(price - stop, 1e-9) * price,
                            balance)
            else:
                stake = balance * stake_amt / 100.0
            if stake <= 0 or balance < stake:
                continue
            amount = stake / price
            fee = stake * fee_rate
            balance -= fee
            open_trades.append({"entry": price, "amount": amount,
                                "stake": stake, "open_i": i, "stop": stop,
                                "peak": price, "fee_paid": fee})
        # equity tracking for drawdown
        equity = balance + sum(t["amount"] * price for t in open_trades)
        peak = max(peak, equity)
        res.max_drawdown = max(res.max_drawdown,
                               (peak - equity) / peak if peak else 0.0)
    for t in open_trades:  # close leftovers at the last close
        _close(res, t, float(advised.iloc[-1]["close"]), "end_of_data",
               fee_rate)
    res.buy_hold_return = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    strat_total = sum(t["pnl"] for t in res.trades)
    # ema crossover benchmark (20/50, single position, fees per turn)
    fast, slow = ema(df["close"], 20), ema(df["close"], 50)
    pos = 0.0
    bench = 0.0
    for i in range(1, len(df)):
        if fast.iloc[i] > slow.iloc[i] and pos == 0.0:
            pos = 1.0
        elif fast.iloc[i] < slow.iloc[i] and pos > 0.0:
            bench += pos * (df["close"].iloc[i] / df["close"].iloc[i - 1]
                            - 1) - 2 * fee_rate
            pos = 0.0
    res.total_pnl = strat_total
    res.beats_buy_hold = strat_total / start_balance > res.buy_hold_return
    res.beats_ema_cross = strat_total / start_balance > bench
    return res


def _trailing(t: dict, price: float, risk) -> float | None:
    if not risk.trailing_stop:
        return None
    frac = (price - t["entry"]) / t["entry"]
    if frac < risk.trailing_stop_positive_offset:
        return None
    return price * (1 - risk.trailing_stop_positive)


def _roi(t: dict, i: int, strategy) -> float | None:
    mins = float(i - t["open_i"])  # candle count as holding-time proxy
    best = None
    for k, v in (strategy.minimal_roi or {}).items():
        if mins >= int(k):
            v = float(v)
            best = v if best is None or v < best else best
    if best is None:
        return None
    return t["entry"] * (1 + best)


def _close(res: BacktestResult, t: dict, price: float, reason: str,
           fee_rate: float):
    pnl = (price - t["entry"]) * t["amount"] - t["fee_paid"] \
        - t["amount"] * price * fee_rate
    res.trades.append({"entry": t["entry"], "exit": price,
                       "pnl": pnl, "roi": pnl / t["stake"],
                       "reason": reason})
    if pnl > 0:
        res.wins += 1
    else:
        res.losses += 1
