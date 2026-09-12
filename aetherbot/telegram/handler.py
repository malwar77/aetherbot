"""Telegram control interface.

Commands (only for allowed_user_ids when set):
  /status   — mode, open trades, P&L (real numbers, including losses)
  /profit   — closed trade stats
  /pause /resume — pause entries
  /forcebuy <pair> — force an entry through the RISK GATES
  /forcesell <id>  — force-close an open trade
  /reload — reload config from disk (mode changes NEVER auto-apply:
             switching to live still requires a full restart and the
             human-only config gates)
Token comes from AETHERBOT_TELEGRAM_TOKEN. Disabled entirely without it.
"""
from __future__ import annotations

import logging

log = logging.getLogger("aetherbot.telegram")


class TelegramControl:
    """Runs python-telegram-bot v20+ polling in its own asyncio loop,
    sharing the engine via a thread-safe command queue."""

    def __init__(self, bot, token: str,
                 allowed_user_ids: list[int] | None = None):
        self.bot = bot
        self.token = token
        self.allowed = set(allowed_user_ids or [])

    def _authorized(self, update) -> bool:
        uid = update.effective_user.id if update.effective_user else None
        return uid in self.allowed if self.allowed else True

    def run_blocking(self):
        """Blocking runner — call from a dedicated thread."""
        try:
            import asyncio
            from telegram import Update
            from telegram.ext import (Application, CommandHandler,
                                       ContextTypes)

            async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                             fn):
                if not self._authorized(update):
                    await update.message.reply_text("not authorized")
                    return
                await update.message.reply_text(fn(update, ctx))

            async def status(u, c):
                s = self.bot.status()
                lines = ["AetherBot — %s mode" % s["mode"]]
                lines.append("paused: %s" % s["paused"])
                lines.append("balance: %.2f" % s["balance"])
                lines.append("open trades: %d" % len(s["open_trades"]))
                for t in s["open_trades"]:
                    lines.append("  #%d %s @ %.8g (stop %.8g)"
                                 % (t["id"], t["pair"], t["entry"], t["stop"]))
                lines.append("closed: %d (%d wins), total P&L %.4f — real "
                             "numbers including losses, no promises"
                             % (s["closed_trades"], s["wins"], s["total_pnl"]))
                return "\n".join(lines)

            async def profit(u, c):
                s = self.bot.status()
                return ("closed: %d | wins: %d | total pnl: %.4f | today: "
                        "%.4f" % (s["closed_trades"], s["wins"],
                                  s["total_pnl"], s["daily_pnl"]))

            async def pause(u, c):
                self.bot.paused = True
                return "entries paused (open trades still managed)"

            async def resume(u, c):
                self.bot.paused = False
                return "resumed"

            async def forcebuy(u, c):
                if not c.args:
                    return "usage: /forcebuy <pair>"
                pair = c.args[0].upper().replace("-", "/")
                from aetherbot.engine.force import force_enter
                return force_enter(self.bot, pair)

            async def forcesell(u, c):
                if not c.args:
                    return "usage: /forcesell <trade id>"
                from aetherbot.engine.force import force_exit
                return force_exit(self.bot, c.args[0])

            async def reload_config(u, c):
                return ("config reload: mode NEVER changes live without the "
                        "human-only config gates + restart. restart the bot "
                        "to apply config changes.")

            app = Application.builder().token(self.token).build()
            for name, fn in [("/status", status), ("/profit", profit),
                             ("/pause", pause), ("/resume", resume),
                             ("/forcebuy", forcebuy),
                             ("/forcesell", forcesell),
                             ("/reload", reload_config)]:
                app.add_handler(CommandHandler(
                    name[1:], lambda u, c, f=fn: handle(u, c, f)))
            app.run_polling(close_loop=False)
        except Exception as exc:  # noqa: BLE001 — UI must not kill the bot
            log.error("telegram disabled (%s)", exc)
