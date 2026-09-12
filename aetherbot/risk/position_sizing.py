"""Position sizing — pure calculator.

The standard fixed-risk formula:
    position_size = (account_balance * risk_pct/100) / (stop_distance)
For percentage-of-balance sizing:
    stake = balance * pct/100

This helper does NOT bypass the RiskManager: aetherbot.risk.risk_manager
independently validates stake amount, stop distance, max exposure and
every other limit before any order is placed. It is a calculator only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingResult:
    stake: float
    amount: float            # base units
    stop_distance: float     # quote units per unit risk
    reason: str = ""


def stake_fixed(amount: float) -> float:
    if amount <= 0:
        raise ValueError("fixed stake must be positive")
    return amount


def stake_percentage(balance: float, pct: float) -> float:
    if balance <= 0 or pct <= 0:
        raise ValueError("balance and pct must be positive")
    return balance * pct / 100.0


def stake_risk_pct(balance: float, risk_pct: float, entry: float,
                   stop_price: float) -> SizingResult:
    """Size so that a stop-out loses exactly risk_pct% of balance.

    amount = (balance * risk_pct/100) / |entry - stop|
    Raises if the stop is invalid (not on the correct side of entry)."""
    if entry <= 0:
        raise ValueError("entry price must be positive")
    if risk_pct <= 0:
        raise ValueError("risk_pct must be positive")
    if stop_price <= 0:
        raise ValueError("stop_price must be positive")
    stop_distance = abs(entry - stop_price)
    if stop_distance == 0:
        raise ValueError("stop cannot equal entry")
    risk_amount = balance * risk_pct / 100.0
    amount = risk_amount / stop_distance
    return SizingResult(stake=amount * entry, amount=amount,
                        stop_distance=stop_distance,
                        reason="fixed-risk sizing")
