"""Weekly backtest cost model."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desk"))
import weekly as W  # noqa: E402


class WeeklyCosts(unittest.TestCase):
    def test_delivery_round_trip_on_5000(self):
        c = W.costs(5000, 0, False) + W.costs(0, 5000, True)
        self.assertAlmostEqual(c, 5 + 5 + 0.75 + 0.31 * 1.18 * 1 + 0.01 * 1.18 + 15.93, delta=0.5)

    def test_dp_charge_only_on_sales(self):
        self.assertLess(W.costs(5000, 0, False), 6)
        self.assertGreater(W.costs(0, 5000, True), 20)


if __name__ == "__main__":
    unittest.main()
