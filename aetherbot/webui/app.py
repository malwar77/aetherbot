"""Read-only Web UI — FastAPI dashboard.

Shows open trades, closed performance (real numbers, including losses),
recent logs, and the safety mode. It is a WATCHING surface: no trade
controls live here by design — controls are CLI/Telegram, both gated.
Run with: aetherbot web --config config.yaml
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import load_config


def create_app(config_path: str = "config/config.example.yaml") -> FastAPI:
    cfg = load_config(config_path)
    app = FastAPI(title="AetherBot Web UI",
                  description="Read-only dashboard. No trade controls "
                              "here by design.")
    db_file = cfg.persistence.db_url.replace("sqlite:///", "")

    @app.get("/api/status", response_class=JSONResponse)
    def status():
        if not Path(db_file).exists():
            return {"mode": "never started", "note":
                    "start the bot first: aetherbot start --config ..."}
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
            "disclaimer": "Historical and dry-run results do not imply "
                          "future performance. Live trading can lead to "
                          "total loss of capital.",
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

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (Path(__file__).parent / "static" / "index.html").read_text()

    return app
