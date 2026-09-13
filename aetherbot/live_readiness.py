"""Live-trading readiness: risk disclosure + config audit.

The human's pre-live checklist. It NEVER switches the bot to live —
`dry_run: false` plus the `live_confirmation` block are human-only
fields (the config loader refuses live via code by design). Here we
only show the disclosure and audit whether a config would pass the
live gates. Risk authority stays with risk_manager.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

RISK_DISCLOSURE: Tuple[str, ...] = (
    "LIVE TRADING RISK DISCLOSURE",
    "",
    "You are about to enable REAL-MONEY crypto trading. Read fully:",
    "",
    "1. You can lose money — including your entire deposit. Crypto is",
    "   volatile 24/7; backtest and dry-run results do NOT predict",
    "   live performance.",
    "2. Futures leverage magnifies losses as much as gains. Small",
    "   adverse moves trigger liquidations, sometimes cascade-wide.",
    "3. Slippage, spreads, funding fees and thin order books make",
    "   live fills worse than dry-run fills. Stops do NOT guarantee",
    "   an exit price — the market can gap or wick through them.",
    "4. Exchange outages, API rate limits, maintenance windows and",
    "   network drops can leave positions unmanaged — including",
    "   overnight and on weekends when crypto never pauses.",
    "5. The RiskManager (max trades, daily loss, drawdown halt,",
    "   cooldown, ROI, trailing stops) limits damage but cannot",
    "   prevent losses, and protection orders can execute at bad",
    "   prices in fast markets.",
    "6. The AI brain is ADVISORY ONLY with negative power at most.",
    "   It can never create, resize or approve a trade.",
    "7. You are solely responsible for legal/tax compliance in your",
    "   jurisdiction and for your exchange's terms of service.",
    "",
    "If you understand and accept ALL of the above, you enable live",
    "mode by editing the config YAML BY HAND — no command, agent or",
    "automation will do it for you (see `aetherbot go-live`).",
)


class LiveReadiness:
    """Audit one BotConfig against every live gate. Read-only."""

    def __init__(self, config) -> None:
        self.cfg = config

    def checks(self) -> List[Tuple[str, bool, str]]:
        mode = self.cfg.mode
        conf = mode.live_confirmation
        out: List[Tuple[str, bool, str]] = []

        out.append((
            "dry_run is false",
            mode.dry_run is False,
            "currently %r — set `dry_run: false` by hand in the YAML"
            % mode.dry_run))

        out.append((
            "live confirmation present",
            conf.confirmed_live is True,
            "set mode.live_confirmation.confirmed_live: true by hand"))

        out.append((
            "risk disclosure accepted",
            conf.risk_disclosure_accepted is True,
            "set mode.live_confirmation.risk_disclosure_accepted: true "
            "by hand, only after reading the disclosure"))

        out.append((
            "disclosure timestamp recorded",
            conf.risk_disclosure_accepted_at is not None,
            "set mode.live_confirmation.risk_disclosure_accepted_at "
            "when you accept"))

        risk = self.cfg.risk
        out.append((
            "daily loss limit is a positive number",
            float(getattr(risk, "daily_loss_limit_pct", 0) or 0) > 0,
            "configure a sane daily loss limit before going live"))

        out.append((
            "trading pairs configured",
            bool(self.cfg.tradeable_pairs()),
            "an empty pair list means the bot would trade nothing"))

        return out

    @property
    def ready(self) -> bool:
        return all(ok for _, ok, _ in self.checks())

    def report(self) -> Dict[str, Any]:
        checks = self.checks()
        return {
            "ready": all(ok for _, ok, _ in checks),
            "checks": [{"name": n, "ok": ok, "detail": d}
                       for n, ok, d in checks],
        }
