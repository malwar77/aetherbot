"""Read-only Web UI — FastAPI dashboard.

Shows open trades, closed performance (real numbers, including losses),
recent logs, and the safety mode. It is a WATCHING surface: no trade
controls live here by design — controls are CLI/Telegram, both gated.
Run with: aetherbot web --config config.yaml
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import load_config


# live market data caches (dashboard polls stay polite)
_candle_cache: dict[tuple, tuple[float, list]] = {}
_ticker_cache: tuple[float, dict] = (0.0, {})
_CANDLE_TTL = 60.0
_TICKER_TTL = 30.0


def _market_exchange(cfg):
    """Public-data exchange instance (no API keys needed for
    OHLCV/tickers on most exchanges; keys come from env if present)."""
    from aetherbot.exchange.exchange import CcxtExchange
    return CcxtExchange(cfg.exchange)


def fetch_candles(cfg, pair: str, timeframe: str,
                  limit: int = 300) -> list[dict]:
    """OHLCV candles via ccxt, TTL-cached. Real exchange data only —
    if the fetch fails the caller sees an honest error, never a
    synthetic chart."""
    import time as _time
    key = (pair, timeframe, limit)
    now = _time.time()
    hit = _candle_cache.get(key)
    if hit and now - hit[0] < _CANDLE_TTL:
        return hit[1]
    ex = _market_exchange(cfg)
    raw = ex.fetch_ohlcv(pair, timeframe, limit=limit)
    data = [{"time": int(c[0] / 1000), "open": c[1], "high": c[2],
             "low": c[3], "close": c[4], "volume": c[5]} for c in raw]
    _candle_cache[key] = (now, data)
    return data


def fetch_tickers(cfg, limit: int = 6) -> dict[str, dict]:
    """Last-price tickers for the first tradeable pairs, TTL-cached."""
    import time as _time
    now = _time.time()
    ts, cached = _ticker_cache
    if cached and now - ts < _TICKER_TTL:
        return cached
    ex = _market_exchange(cfg)
    out = {}
    for pair in cfg.tradeable_pairs()[:limit]:
        t = ex.fetch_ticker(pair)
        out[pair] = {"last": t.get("last"),
                     "pct": t.get("percentage"),
                     "high": t.get("high"), "low": t.get("low")}
    globals()["_ticker_cache"] = (now, out)
    return out


def lan_url() -> str:
    """Best-effort LAN IP of this machine (for the dashboard URL).
    Never raises — falls back to 127.0.0.1."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))  # no packets sent; just routing
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:  # pragma: no cover
        return "127.0.0.1"


def create_app(config_path: str = "config/config.example.yaml") -> FastAPI:
    cfg = load_config(config_path)
    app = FastAPI(title="AetherBot Web UI",
                  description="Read-only dashboard. No trade controls "
                              "here by design.")
    db_file = cfg.persistence.db_url.replace("sqlite:///", "")

    @app.get("/api/status", response_class=JSONResponse)
    def status():
        disclaimer = ("Historical and dry-run results do not imply "
                      "future performance. Live trading can lead to "
                      "total loss of capital.")
        if not Path(db_file).exists():
            return {"mode": "never started", "note":
                    "start the bot first: aetherbot start --config ...",
                    "disclaimer": disclaimer}
        con = sqlite3.connect(db_file)
        con.row_factory = sqlite3.Row
        open_t = con.execute("SELECT * FROM trades WHERE is_open=1").fetchall()
        closed = con.execute("SELECT * FROM trades WHERE is_open=0").fetchall()
        con.close()
        pnl = sum(t["pnl"] or 0 for t in closed)
        wins = sum(1 for t in closed if t["is_win"])
        return {
            "mode": cfg.mode.dry_run and "dry_run" or "live",
            "pairs": cfg.tradeable_pairs(),
            "timeframe": cfg.trading.timeframe,
            "max_open_trades": cfg.trading.max_open_trades,
            "open_trades": [dict(t) for t in open_t],
            "closed_trades": len(closed),
            "wins": wins,
            "losses": len(closed) - wins,
            "total_pnl": pnl,
            # real numbers only — no expected/potential gains, ever
            "disclaimer": disclaimer,
        }

    @app.get("/api/trades", response_class=JSONResponse)
    def trades(limit: int = 50):
        if not Path(db_file).exists():
            raise HTTPException(404, "no database yet")
        con = sqlite3.connect(db_file)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM trades ORDER BY open_date DESC LIMIT ?",
            (limit,)).fetchall()
        con.close()
        return [dict(r) for r in rows]

    @app.get("/api/candles", response_class=JSONResponse)
    def candles(pair: str = "BTC/USDT", timeframe: str = "1h",
                limit: int = 300):
        try:
            return {"pair": pair, "timeframe": timeframe,
                    "candles": fetch_candles(cfg, pair, timeframe, limit)}
        except Exception as exc:  # offline / bad pair — honest error
            raise HTTPException(
                503, f"live market data unavailable: {exc}")

    @app.get("/api/tickers", response_class=JSONResponse)
    def tickers():
        try:
            return {"tickers": fetch_tickers(cfg)}
        except Exception as exc:
            raise HTTPException(503, f"tickers unavailable: {exc}")

    @app.get("/static/lightweight-charts.standalone.production.js")
    def chart_lib():
        path = (Path(__file__).parent / "static" /
                "lightweight-charts.standalone.production.js")
        if not path.exists():
            raise HTTPException(404, "chart library missing")
        return Response(path.read_bytes(),
                        media_type="application/javascript")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "static" / "index.html").read_text()

    return app
