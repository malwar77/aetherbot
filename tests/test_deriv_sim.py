"""Deriv digit-under simulator tests.

Hand-verified against the original DBot XML branch table:
- last digit 5 -> barrier 0 (GUARANTEED LOSS branch)
- last digit 6 -> barrier 1, digit 7 -> 2, 8 -> 3, 9 -> 4
- last digit 0 -> barrier 5, 1 -> 6, 2 -> 7, 3 -> 8, 4 -> 9
- stake sequence on losses: 0.70 -> 7.00 -> 70.00 -> 700.00 (x10)
- target +$1.00, max loss -$1,000.00
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aetherbot.deriv_sim import (DEFAULT_HOUSE, barrier_for,
                                 payout_multiplier, run_session,
                                 run_simulation, summarize)


class BarrierTableTests(unittest.TestCase):
    def test_barrier_matches_xml_branches(self):
        expected = {5: 0, 6: 1, 7: 2, 8: 3, 9: 4,
                    0: 5, 1: 6, 2: 7, 3: 8, 4: 9}
        for digit, barrier in expected.items():
            self.assertEqual(barrier_for(digit), barrier)

    def test_barrier_zero_never_wins(self):
        self.assertEqual(payout_multiplier(0, DEFAULT_HOUSE), 0.0)
        # a barrier-0 round is a guaranteed loss
        class Rigged(random.Random):
            def randrange(self, n):
                # last digit 5 -> barrier 0; ANY next digit loses
                return 5 if self._flag else super().randrange(n)
        # simpler direct proof: win condition is next < 0 -> never
        for next_digit in range(10):
            self.assertFalse(next_digit < barrier_for(5))


class PayoutTests(unittest.TestCase):
    def test_fair_odds_are_ev_neutral(self):
        # payout = 10/barrier; win prob = barrier/10 -> EV = 0 exactly
        for barrier in range(1, 10):
            p = barrier / 10.0
            mult = payout_multiplier(barrier, 1.0)
            ev = p * mult - 1.0
            self.assertAlmostEqual(ev, 0.0, places=10)

    def test_house_edge_makes_ev_negative(self):
        for barrier in range(1, 10):
            p = barrier / 10.0
            mult = payout_multiplier(barrier, DEFAULT_HOUSE)
            self.assertLess(p * mult - 1.0, 0.0)
            self.assertAlmostEqual(p * mult - 1.0, DEFAULT_HOUSE - 1.0,
                                   places=10)


class SessionTests(unittest.TestCase):
    def test_deterministic_with_seed(self):
        a = run_session(random.Random(42))
        b = run_session(random.Random(42))
        self.assertEqual((a.rounds, a.wins, a.losses, a.ended_by,
                          round(a.total_profit, 10)),
                         (b.rounds, b.wins, b.losses, b.ended_by,
                          round(b.total_profit, 10)))

    def test_session_ends_within_bounds(self):
        rng = random.Random(1)
        for _ in range(50):
            s = run_session(rng)
            if s.ended_by == "target":
                self.assertGreaterEqual(s.total_profit, 1.0)
                # single-round overshoot bound: max stake $1,000 at the
                # best payout (barrier 1: mult 9.5) -> +$8,500 max win
                self.assertLess(s.total_profit, 8500.0)
            elif s.ended_by == "max_loss":
                self.assertLessEqual(s.total_profit, -1000.0)
            else:
                self.assertEqual(s.ended_by, "round_cap")
            self.assertEqual(s.rounds, s.wins + s.losses)

    def test_martingale_stake_sequence(self):
        # force all losses: barrier 0 rounds lose regardless of digits
        class AllLose(random.Random):
            def randrange(self, n):
                return 5  # last digit 5 -> barrier 0 -> guaranteed loss
        s = run_session(AllLose(0))
        self.assertEqual(s.stakes, [0.70, 7.00, 70.00, 700.00,
                                    1000.00])
        self.assertEqual(s.ended_by, "max_loss")
        # 0.70 + 7 + 70 + 700 + 1000 = 1777.70 lost
        self.assertAlmostEqual(s.total_profit, -1777.70, places=2)

    def test_stake_capped_at_max_loss(self):
        class AllLose(random.Random):
            def randrange(self, n):
                return 5
        s = run_session(AllLose(0))
        self.assertLessEqual(max(s.stakes), 1000.00)


class SimulationTests(unittest.TestCase):
    def test_stats_shape_and_honesty(self):
        stats = run_simulation(300, seed=7)
        self.assertEqual(stats["sessions"], 300)
        self.assertAlmostEqual(stats["bust_rate"] + stats["target_rate"],
                               1.0, places=6)
        # with a 5% house edge the mean session PnL must be negative
        self.assertLess(stats["mean_session_pnl"], 0)
        # reference band from hand-run simulations (seed 7, 300 sessions)
        self.assertGreater(stats["bust_rate"], 0.05)
        self.assertLess(stats["bust_rate"], 0.30)
        self.assertGreater(stats["max_stake_reached"], 0)

    def test_summarize_is_honest(self):
        text = summarize(100, seed=7)
        self.assertIn("no real trades", text)
        self.assertIn("$1,000", text)


if __name__ == "__main__":
    unittest.main()
