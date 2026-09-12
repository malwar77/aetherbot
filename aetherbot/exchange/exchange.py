"""CCXT exchange wrapper — live trading only, always behind the gates.

Uses the ccxt library's documented unified API:
fetch_ohlcv / create_order / set_leverage / set_sandbox_mode / fetch_balance.
Rate limiting: ccxt's built-in rate limiter (enableRateLimit=True) plus a
simple retry with backoff on RateLimitExceeded / NetworkError.

Spot and USDT-M futures are selected via options.defaultType. Order types:
market, limit, stop-loss / take-profit via ccxt unified params
(stopLossPrice / takeProfitPrice) — NOT every exchange supports these
unified params; check_bot verifies per-exchange support at startup and
falls back to stop-on-tick monitoring by the bot when unsupported
(the bot ALWAYS monitors stops locally as well — exchange-side stops are
an extra safety net, never the only line of defense).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

log = logging.getLogger("aetherbot.exchange")


def _retry(fn, attempts: int = 3, base_delay: float = 1.5):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — re-raise after backoff
            last = exc
            log.warning("exchange call failed (%s), retry %d/%d",
                        exc, i + 1, attempts)
            import time
            time.sleep(base_delay * (2 ** i))
    raise last


class CcxtExchange:
    """Thin, typed wrapper over one ccxt exchange instance."""

    def __init__(self, exchange_cfg, api_key: str | None = None,
                 api_secret: str | None = None):
        import ccxt
        self.cfg = exchange_cfg
        name = exchange_cfg.name.lower()
        if not hasattr(ccxt, name):
            raise ValueError("unknown exchange %r for ccxt" % exchange_cfg.name)
        klass = getattr(ccxt, name)
        self.x: Any = klass({
            "apiKey": api_key or os.environ.get("AETHERBOT_EXCHANGE_API_KEY", ""),
            "secret": api_secret or os.environ.get("AETHERBOT_EXCHANGE_API_SECRET", ""),
            "enableRateLimit": True,
            "options": {"defaultType":
                        "future" if exchange_cfg.market_type == "future"
                        else "spot"},
        })
        if exchange_cfg.sandbox:
            try:
                self.x.set_sandbox_mode(True)
            except Exception as exc:  # noqa: BLE001 — not all support it
                log.warning("sandbox mode not supported on %s: %s", name, exc)

    # ---------- data ----------
    def fetch_ohlcv(self, pair: str, timeframe: str, limit: int = 200):
        return _retry(lambda: self.x.fetch_ohlcv(
            pair, timeframe=timeframe, limit=limit))

    def fetch_ticker(self, pair: str):
        return _retry(lambda: self.x.fetch_ticker(pair))

    # ---------- account ----------
    def fetch_balance(self):
        return _retry(lambda: self.x.fetch_balance())

    def set_leverage(self, pair: str, leverage: int):
        if self.cfg.market_type != "future":
            return
        try:
            _retry(lambda: self.x.set_leverage(leverage, pair))
        except Exception as exc:  # noqa: BLE001 — some spot-only setups
            log.warning("set_leverage failed for %s: %s", pair, exc)

    # ---------- orders ----------
    def market_order(self, pair: str, side: str, amount: float,
                     reduce_only: bool = False):
        return _retry(lambda: self.x.create_order(
            pair, "market", side, amount, None,
            {"reduceOnly": True} if reduce_only else {}))

    def limit_order(self, pair: str, side: str, amount: float, price: float,
                    reduce_only: bool = False):
        return _retry(lambda: self.x.create_order(
            pair, "limit", side, amount, price,
            {"reduceOnly": True} if reduce_only else {}))

    def attach_protection(self, pair: str, amount: float,
                          stop_price: float | None,
                          take_profit_price: float | None) -> dict:
        """Attach exchange-side stop-loss / take-profit via ccxt unified
        params (stopLossPrice / takeProfitPrice). Returns a report; the
        bot always ALSO monitors stops locally, so failure here is logged,
        never fatal."""
        if stop_price is None and take_profit_price is None:
            return {"attached": False, "reason": "nothing to attach"}
        params: dict[str, Any] = {"reduceOnly": True}
        if stop_price is not None:
            params["stopLossPrice"] = stop_price
        if take_profit_price is not None:
            params["takeProfitPrice"] = take_profit_price
        try:
            order = _retry(lambda: self.x.create_order(
                pair, "market", "sell", amount, None, params))
            return {"attached": True, "order": order.get("id")}
        except Exception as exc:  # noqa: BLE001 — optional safety net
            log.warning("exchange-side protection unsupported/failed for "
                        "%s: %s — bot monitors stops locally", pair, exc)
            return {"attached": False, "reason": str(exc)}

    def close_position(self, pair: str, amount: float, side_in: str):
        side = "sell" if side_in == "long" else "buy"
        return self.market_order(pair, side, amount, reduce_only=True)
