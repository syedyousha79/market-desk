"""Offline tests for the news scanner."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desk"))
import common as C  # noqa: E402
import news as N  # noqa: E402

NSE = """<?xml version="1.0"?><rss><channel>
<item><title>HDFC Bank Limited</title><link>https://n/1</link>
<description>HDFC Bank Limited has informed the Exchange about financial results |SUBJECT: Outcome of Board Meeting</description>
<pubDate>10-Nov-2026 10:00:00</pubDate></item>
<item><title>HDFC Bank Limited</title><link>https://n/2</link>
<description>Closure of Trading Window |SUBJECT: Trading Window</description><pubDate>10-Nov-2026 10:05:00</pubDate></item>
<item><title>Slone Infosystems Limited</title><link>https://n/3</link>
<description>results</description><pubDate>10-Nov-2026 10:06:00</pubDate></item>
</channel></rss>"""

ET = """<?xml version="1.0"?><rss><channel>
<item><title>ITC shares jump 3% after price hike</title><link>https://e/1</link><description>x</description>
<pubDate>Tue, 10 Nov 2026 04:30:00 GMT</pubDate></item>
<item><title>Nintendo Switch sales climb</title><link>https://e/2</link><description>x</description>
<pubDate>Tue, 10 Nov 2026 04:31:00 GMT</pubDate></item>
<item><title>Rupee slips as crude rises</title><link>https://e/3</link><description>x</description>
<pubDate>Tue, 10 Nov 2026 04:32:00 GMT</pubDate></item>
<item><title>Tata Motors CV gains 4%</title><link>https://e/4</link><description>x</description>
<pubDate>Tue, 10 Nov 2026 04:33:00 GMT</pubDate></item>
</channel></rss>"""


class NewsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = os.path.join(self.tmp.name, "data")
        os.makedirs(d)
        open(os.path.join(d, "news-NSE.xml"), "w").write(NSE)
        open(os.path.join(d, "news-ET_Markets.xml"), "w").write(ET)
        self._trader = C.TRADER
        C.TRADER = os.path.join(self.tmp.name, "trader")
        os.environ["NSE_OFFLINE"] = d
        self.cfg = json.loads(json.dumps(C.DEFAULTS))
        self.now = C.now_ist("2026-11-10 11:00")

    def tearDown(self):
        C.TRADER = self._trader
        os.environ.pop("NSE_OFFLINE", None)
        self.tmp.cleanup()

    def items(self):
        items, errors = N.collect(self.cfg, self.now, 12)
        return {i["link"]: i for i in items}

    def test_nse_exact_company_match_and_routine(self):
        it = self.items()
        self.assertEqual(it["https://n/1"]["stocks"], ["HDFCBANK"])
        self.assertTrue(it["https://n/1"]["matters"])
        self.assertTrue(it["https://n/2"]["routine"])
        self.assertFalse(it["https://n/2"]["matters"])
        self.assertEqual(it["https://n/3"]["stocks"], [])          # Infosystems is not Infosys

    def test_acronyms_are_whole_word_and_case_sensitive(self):
        it = self.items()
        self.assertEqual(it["https://e/1"]["stocks"], ["ITC"])
        self.assertEqual(it["https://e/2"]["stocks"], [])          # "Switch" is not ITC

    def test_macro_and_tone(self):
        it = self.items()["https://e/3"]
        self.assertIn("macro", it["tags"])
        self.assertTrue(it["matters"])

    def test_tata_motors_cv_is_not_tmpv(self):
        self.assertNotIn("TMPV", self.items()["https://e/4"]["stocks"])

    def test_new_items_only_once(self):
        first = N.new_items(self.cfg, self.now)
        self.assertTrue(first)
        self.assertEqual(N.new_items(self.cfg, self.now), [])


if __name__ == "__main__":
    unittest.main()
