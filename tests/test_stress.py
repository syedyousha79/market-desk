"""Stress tests: bad data, broken files, hostile replies, boundaries, random inputs, scale.
Run: python -m unittest tests.test_stress -v
"""
import csv
import io
import json
import os
import random
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desk"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import journal as J  # noqa: E402
import news as N  # noqa: E402
import paper as P  # noqa: E402
import plan as PL  # noqa: E402
import risk as R  # noqa: E402
import scan as S  # noqa: E402
from test_desk import DAY, PREV, bars_for, chart_json, make_fixture  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data")
        os.makedirs(self.data)
        make_fixture(self.data)
        self._trader = C.TRADER
        C.TRADER = os.path.join(self.tmp.name, "trader")
        os.makedirs(C.TRADER)
        self._load_env = C.load_env
        C.load_env = lambda: {}          # tests never see the real .env (token, chat id)
        os.environ["NSE_OFFLINE"] = self.data
        os.environ["NSE_NO_TELEGRAM"] = "1"
        self.cfg = dict(json.loads(json.dumps(C.DEFAULTS)), max_loss_per_trade=25.0)
        self.cfg["watchlist"] = ["TESTCO"]
        self.write_cfg({"watchlist": ["TESTCO"]})

    def tearDown(self):
        C.TRADER = self._trader
        C.load_env = self._load_env
        os.environ.pop("NSE_OFFLINE", None)
        self.tmp.cleanup()

    def write_cfg(self, d):
        with open(os.path.join(C.TRADER, "config.json"), "w") as f:
            json.dump(d, f)

    def quiet(self, fn, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                code = fn(argv)
            except C.DeskError as e:
                return "DeskError", str(e)
        return code, buf.getvalue()

    def journal_row(self, **kw):
        row = {"id": "x", "date": "2026-11-10", "symbol": "TESTCO", "side": "long", "qty": 1, "entry": 100,
               "stop": 99, "target": 102, "max_loss": 5, "opened_at": "09:45", "status": "closed",
               "exit_price": 99, "exit_at": "10:00", "exit_reason": "stop", "gross_pnl": -1, "charges": 0.1,
               "net_pnl": -1.1, "setup": "orb", "paper": "true", "reply": "yes"}
        row.update(kw)
        return row

    def ready_plan(self, at="2026-11-10 09:40"):
        self.quiet(S.main, ["orb", "--at", at])
        self.quiet(PL.main, ["TESTCO", "--at", at])
        return self.quiet(R.main, ["--at", at])


class BadData(Base):
    def write_chart(self, name, rows):
        with open(os.path.join(self.data, name), "w") as f:
            json.dump(chart_json(rows), f)

    def test_null_bars_are_skipped(self):
        rows = bars_for(DAY, [100.0] * 10)
        rows[3] = (rows[3][0], None, None, None, None, None)
        self.write_chart("NULLCO.NS-5m.json", rows)
        self.assertEqual(len(C.fetch_bars("NULLCO")), 9)

    def test_empty_and_missing_results(self):
        with open(os.path.join(self.data, "EMPTY.NS-5m.json"), "w") as f:
            json.dump({"chart": {"result": None, "error": {"code": "Not Found"}}}, f)
        with self.assertRaises(C.DeskError):
            C.fetch_bars("EMPTY")
        with self.assertRaises(C.DeskError):
            C.fetch_bars("NOFILE")

    def test_no_bars_today(self):
        self.write_chart("OLD.NS-5m.json", bars_for(PREV, [100.0] * 75))
        self.assertEqual(S.orb_for("OLD", C.now_ist("2026-11-10 10:00"), self.cfg)["status"], "no bars today")

    def test_zero_volume_history_does_not_crash(self):
        rows = bars_for(PREV, [100.0] * 75, vols=[0] * 75) + bars_for(DAY, [101, 101, 101, 103] + [103] * 10)
        self.write_chart("ZVOL.NS-5m.json", rows)
        r = S.orb_for("ZVOL", C.now_ist("2026-11-10 10:00"), self.cfg)
        self.assertIn(r["status"], ("breakout", "inside range"))

    def test_scan_survives_bad_symbols_in_watchlist(self):
        self.write_cfg({"watchlist": ["TESTCO", "NOFILE", "  ", "testco"]})
        code, out = self.quiet(S.main, ["orb", "--at", "2026-11-10 09:40"])
        self.assertIn("BREAKOUT TESTCO", out)
        self.assertIn("skipped", out)

    def test_network_failure_becomes_desk_error(self):
        os.environ.pop("NSE_OFFLINE")
        real = C._get

        def boom(url, timeout=20):
            raise OSError("network down")
        C._get = boom
        try:
            with self.assertRaises(C.DeskError):
                C.fetch_bars("RELIANCE")
        finally:
            C._get = real

    def test_broken_news_feed_is_reported_not_fatal(self):
        with open(os.path.join(self.data, "news-NSE.xml"), "w") as f:
            f.write("<rss><channel><item><title>cut off")
        items, errors = N.collect(self.cfg, C.now_ist("2026-11-10 11:00"), 12)
        self.assertTrue(any(e.startswith("NSE") for e in errors))


class BrokenFiles(Base):
    def test_corrupt_plan_json_is_a_clear_error(self):
        with open(os.path.join(C.TRADER, "plan.json"), "w") as f:
            f.write("{not json")
        code, out = self.quiet(R.main, ["--at", "2026-11-10 09:40"])
        self.assertEqual(code, "DeskError")

    def test_corrupt_journal_blocks_instead_of_passing(self):
        with open(J.path(), "w") as f:
            f.write("garbage,,,\n\x00\x01 nonsense\n")
        self.quiet(S.main, ["orb", "--at", "2026-11-10 09:40"])
        self.quiet(PL.main, ["TESTCO", "--at", "2026-11-10 09:40"])
        code, out = self.quiet(R.main, ["--at", "2026-11-10 09:40"])
        self.assertNotEqual(code, 0, "a journal that cannot be read must never give a PASS")

    def test_journal_with_blank_numbers(self):
        J.save([self.journal_row(net_pnl="", charges="", max_loss="")])
        code, out = self.ready_plan()
        self.assertIn(code, (0, 2))

    def test_telegram_without_token_never_raises(self):
        # load_env is stubbed to {} in setUp, so this can never reach the real bot
        self.assertEqual(C.load_env(), {})
        with self.assertRaises(C.DeskError):
            C.telegram_api("getMe")


class HostileReplies(Base):
    def test_reply_matrix(self):
        ok = ["yes", "YES", "Yes!", "y", "yes buy TESTCO", "yes place it", "Yes, go ahead"]
        bad = ["", "no", "yeah", "sure", "ok", "yes to all", "yes SBIN", "yes buy 100",
               "yes but no", "not yes", "yes; rm -rf /", "yesterday", "yes\nyes to all"]
        for r in ok:
            self.assertTrue(P.explicit_yes(r.replace(",", ""), "TESTCO", qty=14), r)
        for r in bad:
            self.assertFalse(P.explicit_yes(r, "TESTCO", qty=14), repr(r))

    def test_yes_must_match_the_side(self):
        self.assertFalse(P.explicit_yes("yes sell", "TESTCO", qty=14, side="long"))
        self.assertTrue(P.explicit_yes("yes sell", "TESTCO", qty=14, side="short"))

    def test_open_refused_after_entry_window(self):
        self.ready_plan()
        code, out = self.quiet(P.main, ["open", "--reply", "yes", "--at", "2026-11-10 15:00"])
        self.assertEqual(code, "DeskError")
        self.assertIn("entry window", out)

    def test_reply_naming_the_right_quantity_is_fine(self):
        self.assertTrue(P.explicit_yes("yes buy 14 TESTCO", "TESTCO", qty=14))
        self.assertFalse(P.explicit_yes("yes buy 15 TESTCO", "TESTCO", qty=14))

    def test_double_open_same_symbol_blocked(self):
        self.ready_plan()
        self.quiet(P.main, ["open", "--reply", "yes", "--at", "2026-11-10 09:40"])
        code, out = self.quiet(P.main, ["open", "--reply", "yes", "--at", "2026-11-10 09:40"])
        self.assertEqual(code, "DeskError")
        self.assertEqual(len(J.trades()), 1)

    def test_live_mode_refuses_paper_open(self):
        self.write_cfg({"watchlist": ["TESTCO"], "paper": False})
        self.ready_plan()
        code, out = self.quiet(P.main, ["open", "--reply", "yes", "--at", "2026-11-10 09:40"])
        self.assertEqual(code, "DeskError")


class Boundaries(Base):
    def verdict_at(self, t):
        self.quiet(S.main, ["orb", "--at", "2026-11-10 09:40"])
        self.quiet(PL.main, ["TESTCO", "--at", "2026-11-10 09:40"])
        code, out = self.quiet(R.main, ["--at", "2026-11-10 " + t])
        return code

    def test_entry_window_edges(self):
        self.assertEqual(self.verdict_at("09:29"), 2)
        self.assertEqual(self.verdict_at("09:30"), 0)
        self.assertEqual(self.verdict_at("14:30"), 0)
        self.assertEqual(self.verdict_at("14:31"), 2)

    def test_holiday_and_weekend_block(self):
        self.write_cfg({"watchlist": ["TESTCO"], "holidays": ["2026-11-10"]})
        self.assertEqual(self.verdict_at("10:00"), 2)

    def test_weekly_budget_counts_earlier_days(self):
        self.write_cfg({"watchlist": ["TESTCO"], "loss_pct_week": 3})
        J.save([self.journal_row(date="2026-11-09", net_pnl=-20)])      # Monday loss
        self.assertEqual(self.verdict_at("09:45"), 2)

    def test_last_week_losses_do_not_count(self):
        J.save([self.journal_row(date="2026-11-06", net_pnl=-90)])      # previous Friday
        self.assertEqual(self.verdict_at("09:45"), 0)


class Fills(Base):
    """Exit rules. Entry 100, stop 99, so 1R = 1 rupee a share."""
    def row(self, side="long", entry=100.0, stop=99.0):
        return {"side": side, "entry": entry, "stop": stop, "qty": 10, "flat_by": "15:15"}

    def bars(self, specs, start="10:00"):
        import datetime as dt
        h, m = map(int, start.split(":"))
        t0 = dt.datetime(2026, 11, 10, h, m, tzinfo=C.IST)
        return [{"t": t0 + dt.timedelta(minutes=5 * i), "o": o, "h": hi, "l": lo, "c": c, "v": 1}
                for i, (o, hi, lo, c) in enumerate(specs)]

    def exit(self, specs, **kw):
        return P.walk(self.row(**kw.pop("row", {})), self.bars(specs, **kw), self.cfg)

    def test_gap_through_stop_fills_at_open(self):
        hit, _ = self.exit([(97.5, 98, 97, 97.8)])
        self.assertEqual((hit[0], hit[2]), (97.5, "stop (gap)"))

    def test_stop_checked_before_profit_in_same_bar(self):
        hit, _ = self.exit([(100, 103, 98.5, 101)])
        self.assertEqual(hit[2], "stop")

    def test_user_scenario_25_to_50_dip_then_100(self):
        # risk Rs 1/share: +1R, +2R, dip to +1.2R (above the lock), +4R, then fall back
        hit, st = self.exit([(100, 101.1, 99.9, 101), (101, 102.1, 100.9, 102), (102, 102.1, 101.2, 101.3),
                             (101.3, 104.1, 101.2, 104), (104, 104.1, 102.9, 103)])
        self.assertEqual(hit[2], "trailing stop")
        self.assertAlmostEqual(hit[0], 103.1)          # 4.1 best minus 1R trail: +3.1R kept
        hit, st = self.exit([(100, 101.1, 99.9, 101), (101, 102.1, 100.9, 102), (102, 102.1, 101.2, 101.3)])
        self.assertIsNone(hit)                         # the dip did not hit the +1.1R lock: hold
        self.assertAlmostEqual(st["stop"], 101.1)

    def test_break_even_after_1r(self):
        hit, _ = self.exit([(100, 101.0, 99.9, 100.8), (100.8, 100.9, 99.8, 99.9)])
        self.assertEqual((hit[0], hit[2]), (100.0, "break-even stop"))

    def test_time_stop_when_nothing_happens(self):
        hit, _ = self.exit([(100, 100.3, 99.8, 100.1)] * 12)
        self.assertEqual((hit[1], hit[2]), ("10:55", "time stop"))

    def test_half_sold_at_2r_and_pnl_counts_both_legs(self):
        hit, st = self.exit([(100, 102.1, 99.9, 102), (102, 102.2, 101.0, 101.1)])
        self.assertEqual(st["partial"], (102.0, "10:00"))
        self.assertEqual(hit[2], "trailing stop")
        row = dict(self.row(), qty=10, partial_qty=5, partial_price=102.0)
        P.close_row(row, hit[0], hit[1], hit[2], self.cfg)
        self.assertAlmostEqual(row["gross_pnl"], 5 * 2.0 + 5 * (hit[0] - 100), places=2)
        self.assertGreater(row["charges"], 0)

    def test_time_stop_uses_clock_minutes_with_missing_bar(self):
        bars = self.bars([(100, 100.3, 99.8, 100.1)] * 13)
        del bars[3]                                    # one bar missing in the first hour
        hit, _ = P.walk(self.row(), bars, self.cfg)
        self.assertEqual((hit[1], hit[2]), ("10:55", "time stop"))

    def test_no_partial_with_one_share(self):
        _, st = P.walk(dict(self.row(), qty=1), self.bars([(100, 102.1, 99.9, 102)]), self.cfg)
        self.assertIsNone(st["partial"])

    def test_bad_news_exits_next_bar(self):
        import datetime as dt
        bars = self.bars([(100, 100.6, 99.8, 100.5)] * 4)
        hit, _ = P.walk(self.row(), bars, self.cfg, [dt.datetime(2026, 11, 10, 10, 7, tzinfo=C.IST)])
        self.assertEqual((hit[1], hit[2]), ("10:10", "bad news"))

    def test_flat_by_exit(self):
        hit, _ = self.exit([(100.2, 100.6, 100.1, 100.5)] * 3, start="15:05")
        self.assertEqual((hit[1], hit[2]), ("15:15", "flat-by"))

    def test_missing_flat_by_bars_still_exit_at_flat_by(self):
        bars = self.bars([(100.2, 100.6, 100.1, 100.5)] * 2, start="15:00")
        bars += self.bars([(100.9, 101, 100.8, 100.9)], start="15:25")
        hit, _ = P.walk(self.row(), bars, self.cfg)
        self.assertEqual(hit, (100.5, "15:15", "flat-by (data gap)"))

    def test_flat_by_bar_still_forming_is_not_a_data_gap(self):
        self.ready_plan()
        self.quiet(P.main, ["open", "--reply", "yes", "--at", "2026-11-10 09:40"])
        P.update(C.with_money(C.load_config()), C.now_ist("2026-11-10 15:16"))   # 15:15 bar not closed yet
        self.assertNotEqual(J.trades()[0]["exit_reason"], "flat-by (data gap)")

    def test_short_side_mirrors(self):
        r = {"row": {"side": "short", "entry": 100, "stop": 101}}
        self.assertEqual(self.exit([(100, 101.5, 99.5, 101)], **dict(r))[0][2], "stop")
        hit, st = self.exit([(100, 100.2, 98.9, 99), (99, 99.1, 97.9, 98)], **dict(r))
        self.assertIsNone(hit)
        self.assertAlmostEqual(st["stop"], 98.9)       # trails 1R above the best (lowest) price


class Agents(Base):
    def test_prefill_recheck_refuses_after_bad_news(self):
        self.ready_plan()
        C.post("news", C.now_ist("2026-11-10 09:40"), stocks=["TESTCO"], text="probe", source="x",
               time="2026-11-10T09:45:00+05:30", bearish=True, bullish=False)
        code, out = self.quiet(P.main, ["open", "--reply", "yes", "--at", "2026-11-10 09:40"])
        self.assertEqual(code, "DeskError")
        self.assertIn("bad news", out)

    def test_losing_streak_pauses_and_resume_lifts_it(self):
        self.write_cfg({"watchlist": ["TESTCO"], "loss_pct_week": 100, "loss_pct_day": 100,
                        "max_trades_per_day": 9})
        J.save([self.journal_row(id=str(i), date="2026-11-09", exit_at="1%d:00" % i, net_pnl=-1) for i in range(3)])
        self.assertEqual(self.ready_plan()[0], 2)
        self.quiet(R.main, ["--resume", "--at", "2026-11-10 09:40"])
        self.assertEqual(self.ready_plan()[0], 0)

    def test_max_open_blocks_second_position(self):
        self.write_cfg({"watchlist": ["TESTCO"], "max_trades_per_day": 9, "loss_pct_day": 100})
        J.save([self.journal_row(status="open", net_pnl="", max_loss=1, symbol="OTHER")])
        code, out = self.ready_plan()
        self.assertEqual(code, 2)
        self.assertIn("already open", out)

    def test_signal_needs_fresh_bar_and_respects_news_veto(self):
        cfg = C.load_config()
        self.assertEqual([e["symbol"] for e in __import__("signals").ideas(cfg, C.now_ist("2026-11-10 09:40"))], ["TESTCO"])
        self.assertEqual(__import__("signals").ideas(cfg, C.now_ist("2026-11-10 09:50")), [])     # stale
        C.post("news", C.now_ist("2026-11-10 09:39"), stocks=["TESTCO"], text="probe", source="x",
               time="2026-11-10T09:39:00+05:30", bearish=True, bullish=False)
        self.assertEqual(__import__("signals").ideas(cfg, C.now_ist("2026-11-10 09:40")), [])     # vetoed

    def test_signal_respects_brief_avoid_list(self):
        C.write_json("brief.json", {"date": "2026-11-10", "avoid": [{"symbol": "TESTCO", "why": "results"}]})
        self.assertEqual(__import__("signals").ideas(C.load_config(), C.now_ist("2026-11-10 09:40")), [])


class RandomInvariants(Base):
    def test_sizing_invariants_over_random_inputs(self):
        rng = random.Random(7)
        for _ in range(3000):
            cfg = json.loads(json.dumps(C.DEFAULTS))
            cfg["account_inr"] = rng.choice([50, 250, 1000, 4400, 100000])
            cfg["max_loss_per_trade"] = rng.choice([5, 25, 100, 1000])
            cfg["mis_leverage"] = rng.choice([1, 5])
            entry = round(rng.uniform(5, 30000), 2)
            dist = entry * rng.uniform(0.0005, 0.05)
            side = rng.choice([1, -1])
            stop = round(entry - side * dist, 2)
            if stop <= 0 or stop == entry:
                continue
            target = round(entry + side * dist * rng.uniform(0.5, 4), 2)
            s = PL.size(entry, stop, target, cfg)
            self.assertGreaterEqual(s["qty"], 0)
            if s["qty"]:
                self.assertLessEqual(s["max_loss"], cfg["max_loss_per_trade"] + 1e-6, (entry, stop, cfg))
                self.assertLessEqual(s["value"], cfg["account_inr"] * cfg["mis_leverage"] + 1e-6)
                self.assertGreater(s["charges_at_stop"]["total"], 0)
                self.assertLess(s["net_gain_at_target"], s["qty"] * abs(target - entry))

    def test_charges_never_negative_and_grow_with_size(self):
        rng = random.Random(3)
        prev = 0
        for v in sorted(rng.uniform(1, 5_000_000) for _ in range(500)):
            t = C.charges(v, v, self.cfg)["total"]
            self.assertGreaterEqual(t, prev - 1e-9)
            prev = t

    def test_random_bar_walks_always_exit_by_flat(self):
        rng = random.Random(11)
        f = Fills()
        f.cfg = self.cfg
        for _ in range(500):
            px, specs = 100.0, []
            for _ in range(70):
                o = px
                c = max(1, o + rng.gauss(0, 0.6))
                specs.append((o, max(o, c) + abs(rng.gauss(0, 0.3)), min(o, c) - abs(rng.gauss(0, 0.3)), c))
                px = c
            hit, _ = P.walk(f.row(), f.bars(specs, start="09:45"), self.cfg)
            self.assertIsNotNone(hit, "a trade must always be closed by the flat-by time")
            self.assertLessEqual(hit[1], "15:15")


class Scale(Base):
    def test_200_symbol_scan_is_fast(self):
        with open(os.path.join(self.data, "TESTCO.NS-5m.json")) as f:
            body = f.read()
        syms = ["SYM%03d" % i for i in range(200)]
        for s in syms:
            with open(os.path.join(self.data, "%s.NS-5m.json" % s), "w") as f:
                f.write(body)
        self.write_cfg({"watchlist": syms})
        t = time.time()
        code, out = self.quiet(S.main, ["orb", "--at", "2026-11-10 09:40"])
        self.assertLess(time.time() - t, 10)
        self.assertIn("200 breakout(s)", out)

    def test_large_journal(self):
        J.save([self.journal_row(id=str(i), date="2026-10-%02d" % (1 + i % 28)) for i in range(20000)])
        t = time.time()
        s = J.summary()
        self.assertEqual(s["trades"], 20000)
        self.assertLess(time.time() - t, 5)


if __name__ == "__main__":
    unittest.main()
