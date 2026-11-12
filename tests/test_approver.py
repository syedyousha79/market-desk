"""Approver and guardian tests (offline)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desk"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approver as A  # noqa: E402
import common as C  # noqa: E402
import journal as J  # noqa: E402
import signals as SG  # noqa: E402
from test_stress import Base  # noqa: E402

AT = "2026-11-10 09:40"


class ApproverTest(Base):
    def idea(self):
        ideas = SG.ideas(C.load_config(), C.now_ist(AT))
        self.assertEqual(len(ideas), 1)
        return ideas[0]

    def money(self, **over):
        self.write_cfg(dict({"watchlist": ["TESTCO"]}, **over))
        return C.with_money(C.load_config())

    def test_approver_takes_a_qualifying_trade(self):
        cfg = self.money()
        row = A.decide(self.idea(), cfg, C.now_ist(AT))
        self.assertIsNotNone(row)
        self.assertEqual(J.trades()[0]["reply"], "auto: approver")
        self.assertEqual(C.events(C.now_ist(AT).date(), "approved")[0]["symbol"], "TESTCO")

    def test_approver_refuses_live_mode(self):
        cfg = self.money(paper=False)
        self.assertIsNone(A.decide(self.idea(), cfg, C.now_ist(AT)))
        self.assertIn("paper mode", C.events(C.now_ist(AT).date(), "rejected")[0]["reason"])

    def test_approver_says_no_when_risk_blocks(self):
        cfg = self.money(max_trades_per_day=0)
        self.assertIsNone(A.decide(self.idea(), cfg, C.now_ist(AT)))
        self.assertEqual(J.trades(), [])

    def test_stop_file_halts_and_closes(self):
        cfg = self.money()
        A.decide(self.idea(), cfg, C.now_ist(AT))
        open(C.state_path("STOP"), "w").close()
        h = A.guard(C.with_money(C.load_config()), C.now_ist("2026-11-10 10:00"))
        self.assertEqual(h["until"], "review")
        self.assertEqual(J.trades()[0]["status"], "closed")
        self.assertIsNone(A.decide({"symbol": "TESTCO", "entry": 103, "or_low": 100, "or_high": 102},
                                   cfg, C.now_ist("2026-11-10 10:05")))
        A.resume(C.now_ist("2026-11-10 10:10"))
        self.assertIsNone(A.halted(C.now_ist("2026-11-10 10:10")))
        self.assertFalse(os.path.exists(C.state_path("STOP")))

    def test_day_loss_halts_for_today_only(self):
        J.save([self.journal_row(date="2026-11-10", net_pnl=-30)])
        h = A.guard(self.money(), C.now_ist("2026-11-10 10:00"))
        self.assertEqual(h["until"], "day")
        self.assertIsNone(A.halted(C.now_ist("2026-11-11 10:00")))

    def test_kill_switch_halts_until_review(self):
        J.save([self.journal_row(date="2026-11-09", net_pnl=-200)])       # 20% of a 1,000 account
        h = A.guard(self.money(), C.now_ist("2026-11-10 10:00"))
        self.assertIn("kill switch", h["reason"])
        self.assertIsNotNone(A.halted(C.now_ist("2026-11-12 10:00")))


if __name__ == "__main__":
    unittest.main()
