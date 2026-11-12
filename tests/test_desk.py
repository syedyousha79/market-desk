"""Offline tests for the NSE desk. Run: python -m unittest discover -s tests -v"""
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desk"))
import common as C  # noqa: E402
import journal as J  # noqa: E402
import paper as P  # noqa: E402
import plan as PL  # noqa: E402
import risk as R  # noqa: E402
import scan as S  # noqa: E402

DAY = dt.date(2026, 11, 10)      # a Tuesday
PREV = dt.date(2026, 11, 9)


def bars_for(day, closes, highs=None, lows=None, vols=None, opens=None):
    out = []
    t0 = dt.datetime.combine(day, C.OPEN, C.IST)
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        h = highs[i] if highs else max(o, c) + 0.2
        l = lows[i] if lows else min(o, c) - 0.2
        out.append((int((t0 + dt.timedelta(minutes=5 * i)).timestamp()), o, h, l, c, vols[i] if vols else 1000))
    return out


def chart_json(rows):
    return {"chart": {"result": [{"timestamp": [r[0] for r in rows], "indicators": {"quote": [{
        "open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
        "close": [r[4] for r in rows], "volume": [r[5] for r in rows]}]}}]}}


def make_fixture(d):
    prev = bars_for(PREV, [100.0] * 75)
    # today: range 09:15-09:30 is 100-102, 09:30 inside, 09:35 breaks out on 3x volume, then trends to 105+
    closes = [101, 101.5, 101.8, 101.5, 102.6, 103.0, 103.5, 104.0, 104.6, 105.3] + [105.5] * 65
    highs = [102, 101.8, 102, 101.9, 102.8, 103.2, 103.7, 104.2, 104.8, 105.5] + [105.7] * 65
    lows = [100, 101.2, 101.4, 101.2, 101.6, 102.7, 103.2, 103.7, 104.3, 104.9] + [105.3] * 65
    vols = [2000, 1500, 1500, 1200, 3000] + [1000] * 70
    today = bars_for(DAY, closes, highs, lows, vols)
    with open(os.path.join(d, "TESTCO.NS-5m.json"), "w") as f:
        json.dump(chart_json(prev + today), f)


class DeskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        os.makedirs(self.data)
        make_fixture(self.data)
        self._trader = C.TRADER
        C.TRADER = os.path.join(self.tmp.name, "trader")
        os.environ["NSE_OFFLINE"] = self.data
        os.environ["NSE_NO_TELEGRAM"] = "1"
        self.cfg = json.loads(json.dumps(C.DEFAULTS))
        self.cfg["watchlist"] = ["TESTCO"]
        os.makedirs(C.TRADER)
        with open(os.path.join(C.TRADER, "config.json"), "w") as f:
            json.dump({"watchlist": ["TESTCO"]}, f)

    def tearDown(self):
        C.TRADER = self._trader
        os.environ.pop("NSE_OFFLINE", None)
        self.tmp.cleanup()

    def run_quiet(self, fn, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = fn(argv)
        return code, buf.getvalue()

    def test_charges_round_trip(self):
        ch = C.charges(10000, 10000, self.cfg)
        self.assertAlmostEqual(ch["brokerage"], 6.0)
        self.assertAlmostEqual(ch["stt"], 2.5)
        self.assertTrue(10 < ch["total"] < 11.5, ch)

    def test_brokerage_cap(self):
        ch = C.charges(1_000_000, 1_000_000, self.cfg)
        self.assertAlmostEqual(ch["brokerage"], 40.0)

    def test_ist_is_fixed_offset(self):
        t = dt.datetime(2026, 7, 1, 3, 45, tzinfo=dt.timezone.utc).astimezone(C.IST)
        self.assertEqual(t.strftime("%H:%M"), "09:15")

    def test_orb_waits_for_range_then_finds_breakout(self):
        early = S.orb_for("TESTCO", C.now_ist("2026-11-10 09:25"), self.cfg)
        self.assertEqual(early["status"], "opening range still forming")
        r = S.orb_for("TESTCO", C.now_ist("2026-11-10 09:45"), self.cfg)
        self.assertEqual(r["status"], "breakout")
        self.assertEqual(r["trigger_bar"], "09:35")
        self.assertEqual((r["or_low"], r["or_high"]), (100, 102))

    def test_unclosed_bar_is_ignored(self):
        r = S.orb_for("TESTCO", C.now_ist("2026-11-10 09:38"), self.cfg)
        self.assertEqual(r["status"], "inside range")

    def test_size_keeps_loss_inside_limit_with_charges(self):
        s = PL.size(102.6, 101.0, 105.0, self.cfg)
        self.assertGreater(s["qty"], 0)
        self.assertLessEqual(s["max_loss"], self.cfg["max_loss_per_trade"])
        self.assertLessEqual(s["value"], self.cfg["account_inr"] * self.cfg["mis_leverage"])

    def test_size_refuses_when_one_share_is_too_much(self):
        s = PL.size(3000, 2970, 3045, self.cfg)
        self.assertEqual(s["qty"], 0)

    def test_explicit_yes(self):
        self.assertTrue(P.explicit_yes("yes", "TESTCO"))
        self.assertTrue(P.explicit_yes("Yes, buy TESTCO".replace(",", ""), "TESTCO"))
        self.assertFalse(P.explicit_yes("sure", "TESTCO"))
        self.assertFalse(P.explicit_yes("yes to all", "TESTCO"))
        self.assertFalse(P.explicit_yes("yes SBIN", "TESTCO"))

    def full_flow_to_open(self):
        self.run_quiet(S.main, ["orb", "--at", "2026-11-10 09:45"])
        self.run_quiet(PL.main, ["TESTCO", "--at", "2026-11-10 09:45"])
        code, out = self.run_quiet(R.main, ["--at", "2026-11-10 09:45"])
        self.assertEqual(code, 0, out)
        code, out = self.run_quiet(P.main, ["open", "--reply", "yes", "--at", "2026-11-10 09:45"])
        self.assertEqual(code, 0, out)

    def test_risk_blocks_before_entry_window(self):
        self.run_quiet(S.main, ["orb", "--at", "2026-11-10 09:45"])
        self.run_quiet(PL.main, ["TESTCO", "--at", "2026-11-10 09:45"])
        code, out = self.run_quiet(R.main, ["--at", "2026-11-10 09:20"])
        self.assertEqual(code, 2)
        self.assertIn("entry window", out)

    def test_open_needs_yes(self):
        self.run_quiet(S.main, ["orb", "--at", "2026-11-10 09:45"])
        self.run_quiet(PL.main, ["TESTCO", "--at", "2026-11-10 09:45"])
        self.run_quiet(R.main, ["--at", "2026-11-10 09:45"])
        code, out = self.run_quiet(P.main, ["open", "--reply", "ok", "--at", "2026-11-10 09:45"])
        self.assertEqual(code, 3)
        self.assertEqual(J.trades(), [])

    def test_paper_trade_hits_target_and_journals_net(self):
        self.full_flow_to_open()
        rows = J.trades()
        self.assertEqual(rows[0]["status"], "open")
        ev = P.update(C.load_config(), C.now_ist("2026-11-10 11:00"))
        self.assertEqual(len(ev), 1)
        r = J.trades()[0]
        self.assertEqual(r["exit_reason"], "target")
        self.assertLess(float(r["net_pnl"]), float(r["gross_pnl"]))

    def test_second_trade_same_day_is_blocked(self):
        self.full_flow_to_open()
        self.run_quiet(PL.main, ["TESTCO", "--at", "2026-11-10 10:00"])
        code, out = self.run_quiet(R.main, ["--at", "2026-11-10 10:00"])
        self.assertEqual(code, 2)
        self.assertIn("trade(s) today", out)

    def test_plan_change_after_pass_is_not_ready(self):
        self.run_quiet(S.main, ["orb", "--at", "2026-11-10 09:45"])
        self.run_quiet(PL.main, ["TESTCO", "--at", "2026-11-10 09:45"])
        self.run_quiet(R.main, ["--at", "2026-11-10 09:45"])
        plan = C.read_json("plan.json")
        plan["qty"] += 5
        C.write_json("plan.json", plan)
        ok, why = P.ready(plan, self.cfg, C.now_ist("2026-11-10 09:45"))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
