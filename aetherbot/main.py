#!/usr/bin/env python3
"""AetherBot CLI.

Commands:
  start             run the bot (dry-run by default; live only with the
                    human config gates — see config.example.yaml)
  backtest          backtest a strategy over downloaded data
  download-data     download OHLCV history to data/
  create-strategy   scaffold a new strategy in strategies/
  web               run the read-only Web UI dashboard

Educational software. Live trading can lead to TOTAL LOSS of capital.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import TradingModeError, load_config
from .engine.strategy.resolver import create_strategy, load_strategy


def _setup_logging(cfg) -> None:
    os.makedirs(os.path.dirname(cfg.logging.file) or ".", exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(cfg.logging.file)])


def cmd_start(args) -> int:
    cfg = load_config(args.config)
    _setup_logging(cfg)
    try:
        strategy = load_strategy(args.strategy)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    from .engine.bot import AetherBot
    try:
        bot = AetherBot(cfg, strategy, once=args.once)
    except TradingModeError as exc:
        print("REFUSING TO START: %s" % exc)
        return 2
    threads = []
    if cfg.telegram.enabled:
        token = os.environ.get("AETHERBOT_TELEGRAM_TOKEN")
        if not token:
            print("telegram.enabled is true but AETHERBOT_TELEGRAM_TOKEN "
                  "is not set — telegram disabled")
        else:
            import threading
            from .telegram.handler import TelegramControl
            tg = TelegramControl(bot, token, cfg.telegram.allowed_user_ids)
            t = threading.Thread(target=tg.run_blocking, daemon=True)
            t.start()
            threads.append(t)
    if cfg.webui.enabled and not args.no_web:
        import threading
        from .webui.app import create_app
        import uvicorn
        app = create_app(args.config)
        t = threading.Thread(
            target=uvicorn.run, args=(app,),
            kwargs={"host": cfg.webui.host, "port": cfg.webui.port,
                    "log_level": "warning"}, daemon=True)
        t.start()
        threads.append(t)
    try:
        bot.run(poll_seconds=args.poll)
    except KeyboardInterrupt:
        bot.stop()
    return 0


def cmd_backtest(args) -> int:
    cfg = load_config(args.config)
    strategy = load_strategy(args.strategy)
    import pandas as pd
    from .backtest.backtester import backtest
    from .data.data import CandleFeed
    feed = CandleFeed(source="file", pairs=[args.pair])
    df = feed.ohlcv(args.pair, cfg.trading.timeframe, limit=10000)
    res = backtest(cfg, strategy, df, args.pair,
                   fee_rate=args.fee / 10000.0)
    print(res.summary())
    for t in res.trades[-10:]:
        print("  trade entry=%.8g exit=%.8g pnl=%.4f (%s)"
              % (t["entry"], t["exit"], t["pnl"], t["reason"]))
    return 0


def cmd_download(args) -> int:
    cfg = load_config(args.config)
    from .data.data import download_ohlcv
    from .exchange.exchange import CcxtExchange
    ex = CcxtExchange(cfg.exchange)  # public data — no keys required
    for pair in cfg.tradeable_pairs():
        path = download_ohlcv(ex, pair, args.timeframe, args.days)
        print("downloaded %s -> %s" % (pair, path))
    return 0


def cmd_create(args) -> int:
    path = create_strategy(args.name)
    print("created strategy template: %s" % path)
    return 0


def cmd_web(args) -> int:
    cfg = load_config(args.config)
    import uvicorn
    from .webui.app import create_app
    uvicorn.run(create_app(args.config), host=cfg.webui.host,
                port=cfg.webui.port)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="aetherbot",
                                description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="run the bot")
    s.add_argument("--config", default="config/config.example.yaml")
    s.add_argument("--strategy", default="RsiEmaCross")
    s.add_argument("--once", action="store_true",
                   help="single cycle then exit (smoke test)")
    s.add_argument("--poll", type=float, default=30.0)
    s.add_argument("--no-web", action="store_true")
    s.set_defaults(fn=cmd_start)

    b = sub.add_parser("backtest", help="backtest a strategy")
    b.add_argument("--config", default="config/config.example.yaml")
    b.add_argument("--strategy", default="RsiEmaCross")
    b.add_argument("--pair", default="BTC/USDT")
    b.add_argument("--fee", type=float, default=10.0,
                   help="fee in basis points per side (default 10 = 0.1%%)")
    b.set_defaults(fn=cmd_backtest)

    d = sub.add_parser("download-data", help="download OHLCV history")
    d.add_argument("--config", default="config/config.example.yaml")
    d.add_argument("--timeframe", default="15m")
    d.add_argument("--days", type=int, default=90)
    d.set_defaults(fn=cmd_download)

    c = sub.add_parser("create-strategy", help="scaffold a new strategy")
    c.add_argument("name")
    c.set_defaults(fn=cmd_create)

    w = sub.add_parser("web", help="run the read-only web dashboard")
    w.add_argument("--config", default="config/config.example.yaml")
    w.set_defaults(fn=cmd_web)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
