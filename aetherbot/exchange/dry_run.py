"""Dry-run (paper) exchange simulator — the DEFAULT execution venue.

Implements the same narrow interface the engine uses from CcxtExchange
(fetch_ohlcv / market_order / close_position / attach_protection /
fetch_balance). Fills happen at the latest candle close or provided
ticker price, with configurable simulated fees. No real orders, no real
money, ever.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("aetherbot.dryrun")


@dataclass
class SimTrade:
    pair: str
    side: str
    amount: float
    entry_price: float
    fee_paid: float = 0.0
    protections: dict = field(default_factory=dict)


class DryRunExchange:
    paper = True

    def __init__(self, start_balance: float = 10000.0,
                 fee_rate: float = 0.001, candles=None):
        """candles: optional {pair: list[[ts,o,h,l,c,v],...]} fixture for
        offline use; otherwise data comes from the data feed injected by
        the engine."""
        self.start_balance = float(start_balance)
        self.balance = float(start_balance)
        self.fee_rate = fee_rate
        self._candles = candles or {}
        self._positions: dict[str, SimTrade] = {}
        self.orders: list[dict] = []

    # ---------- data (offline fixtures) ----------
    def fetch_ohlcv(self, pair: str, timeframe: str, limit: int = 200):
        if pair in self._candles:
            return self._candles[pair][-limit:]
        raise RuntimeError("dry-run exchange has no candle fixture for %s — "
                           "the engine feeds candles via update_market()"
                           % pair)

    def update_market(self, pair: str, candles: list):
        self._candles[pair] = candles

    def last_price(self, pair: str) -> float:
        return float(self._candles[pair][-1][4])

    # ---------- account ----------
    def fetch_balance(self):
        return {"total": {"USDT": self.balance}}

    # ---------- orders (simulated) ----------
    def market_order(self, pair: str, side: str, amount: float,
                     reduce_only: bool = False, price: float | None = None):
        px = price if price is not None else self.last_price(pair)
        fee = amount * px * self.fee_rate
        if not reduce_only:
            self.balance -= fee
            t = SimTrade(pair=pair, side=side, amount=amount,
                         entry_price=px, fee_paid=fee)
            self._positions[pair] = t
        else:
            t = self._positions.pop(pair, None)
            if t:
                self.balance -= fee
        order = {"id": len(self.orders) + 1, "pair": pair, "side": side,
                 "amount": amount, "price": px, "fee": fee,
                 "status": "closed", "simulated": True}
        self.orders.append(order)
        log.info("[dry-run] %s %s %.6f %s @ %.8g (fee %.4f)",
                 "close" if reduce_only else "open", side, amount, pair, px,
                 fee)
        return order

    def attach_protection(self, pair: str, amount: float, stop_price, tp_price):
        t = self._positions.get(pair)
        if t:
            t.protections = {"stop_price": stop_price,
                             "take_profit_price": tp_price}
        return {"attached": bool(t), "simulated": True}

    def close_position(self, pair: str, amount: float, side_in: str,
                       price: float | None = None):
        side = "sell" if side_in == "long" else "buy"
        return self.market_order(pair, side, amount, reduce_only=True,
                                 price=price)

    # ---------- valuation ----------
    def unrealized_pnl(self, pair: str) -> float:
        t = self._positions.get(pair)
        if not t:
            return 0.0
        px = self.last_price(pair)
        diff = (px - t.entry_price) if t.side == "long" \
            else (t.entry_price - px)
        return diff * t.amount
