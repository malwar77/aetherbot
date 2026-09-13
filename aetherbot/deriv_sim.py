"""Deriv digit-under martingale simulator — honesty demo (paper only).

Faithful port of the "20$ DERIV AUTO BOT" DBot strategy analyzed from
its XML (see Bot Files / deriv_bot_analysis.md on Drive):

- Every tick, read the last digit of Volatility 100 Index.
- Barrier = (last_digit - 5) % 10; buy DIGITUNDER (win if the NEXT
  digit is strictly below the barrier).
- Lose -> stake x 10 (martingale). Win -> stake resets to base.
- Session ends at profit >= TARGET (+$1) or <= MAXIMUM LOSS (-$1,000).

This module NEVER places trades. It simulates the strategy with a
uniform random number generator so the expected-value drain and the
bust probability are visible before anyone risks real money on it.
Digits on Deriv synthetics are uniform by design, which is exactly
what this sim assumes.

House edge: Deriv pays digit-under contracts at slightly below fair
odds. `house_factor` (default 0.95) models that: payout multiplier =
house_factor * 10 / barrier, so every bet has EV = house_factor - 1
(-5% per staked dollar by default). barrier 0 (last digit 5) can
never win — the original bot bets it anyway.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

BASE_STAKE = 0.70
MARTINGALE = 10.0
TARGET = 1.00
MAXIMUM_LOSS = 1000.00
DEFAULT_HOUSE = 0.95  # payout factor vs fair odds (fair = 1.0)

# (last_digit - 5) % 10 — verified against the XML branch table
def barrier_for(last_digit: int) -> int:
    return (last_digit - 5) % 10


def payout_multiplier(barrier: int, house_factor: float) -> float:
    """Deriv-style digit-under payout: fair odds 10/barrier scaled by
    the house factor. barrier 0 is unwinnable and has no payout."""
    if barrier <= 0:
        return 0.0
    return house_factor * 10.0 / barrier


@dataclass
class Session:
    """One bot session from start to TARGET / MAXIMUM LOSS."""
    rounds: int = 0
    wins: int = 0
    losses: int = 0
    stakes: list = field(default_factory=list)
    total_profit: float = 0.0
    ended_by: str = ""  # "target" | "max_loss" | "round_cap"


def run_session(rng: random.Random,
                base_stake: float = BASE_STAKE,
                martingale: float = MARTINGALE,
                target: float = TARGET,
                max_loss: float = MAXIMUM_LOSS,
                house_factor: float = DEFAULT_HOUSE,
                max_rounds: int = 100) -> Session:
    """Simulate one session. Digits are uniform i.i.d. (Deriv synthetics
    are designed to be random — no pattern beats this in expectation)."""
    s = Session()
    stake = base_stake
    while s.rounds < max_rounds:
        s.rounds += 1
        last_digit = rng.randrange(10)
        barrier = barrier_for(last_digit)
        next_digit = rng.randrange(10)
        s.stakes.append(stake)
        win = next_digit < barrier  # digit under; barrier 0 never wins
        if win:
            s.wins += 1
            s.total_profit += stake * (payout_multiplier(barrier,
                                                        house_factor) - 1.0)
            stake = base_stake
        else:
            s.losses += 1
            s.total_profit -= stake
            stake = min(stake * martingale, max_loss)  # bot caps at max loss
        if s.total_profit >= target:
            s.ended_by = "target"
            return s
        if s.total_profit <= -max_loss:
            s.ended_by = "max_loss"
            return s
    s.ended_by = "round_cap"
    return s


def run_simulation(sessions: int = 1000, seed: int = 7,
                  **session_kwargs) -> dict:
    """Monte Carlo over many sessions. Returns aggregate, honest stats."""
    rng = random.Random(seed)
    results = [run_session(rng, **session_kwargs) for _ in range(sessions)]
    busts = [r for r in results if r.ended_by == "max_loss"]
    targets = [r for r in results if r.ended_by == "target"]
    mean_pnl = sum(r.total_profit for r in results) / len(results)
    max_stake = max(r for s in results for r in s.stakes)
    return {
        "sessions": sessions,
        "bust_rate": len(busts) / len(results),
        "target_rate": len(targets) / len(results),
        "mean_session_pnl": round(mean_pnl, 2),
        "mean_rounds": round(sum(r.rounds for r in results) / len(results), 1),
        "max_stake_reached": round(max_stake, 2),
        "total_wins": sum(r.wins for r in results),
        "total_losses": sum(r.losses for r in results),
    }


def summarize(sessions: int = 1000, seed: int = 7, **session_kwargs) -> str:
    stats = run_simulation(sessions, seed, **session_kwargs)
    return (
        "Deriv digit-under martingale — {sessions} simulated sessions\n"
        "  bust (hit -$1,000):   {bust_rate:.1%}\n"
        "  target (+$1 reached): {target_rate:.1%}\n"
        "  mean session PnL:     ${mean_session_pnl}\n"
        "  mean rounds/session:  {mean_rounds}\n"
        "  max stake reached:    ${max_stake_reached}\n"
        "  wins/losses:          {total_wins}/{total_losses}\n"
        "The strategy risks $1,000 to win $1 on random digits with a "
        "house edge. Educational simulation only — no real trades."
    ).format(**stats)


if __name__ == "__main__":  # pragma: no cover
    print(summarize(2000))
