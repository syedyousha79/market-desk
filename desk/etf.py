"""Index ETF switching backtests on real daily prices: no stock list, so no survivorship bias.

    python desk/etf.py [--start 2015-01-01] [--account 10000]

Strategies (rules fixed in advance), decided at each month-end close, traded at the next open:
  trend : hold the Nifty ETF (NIFTYBEES) while Nifty closes above its 200-day average, else cash
  dual  : hold Nifty if its 12-month return beats cash and it is above its 200-day average;
          else gold (GOLDBEES) if gold's 12-month return is positive; else cash
  hold  : buy the Nifty ETF once and never sell (the benchmark)
Cash earns 6% a year (a liquid-fund stand-in). Charges as weekly.costs (conservative for ETFs).
"""
import argparse
import datetime as dt
import sys

import common as C
import weekly as W

CASH_YIELD = 0.06


def daily(sym):
    """Daily bars with bad prints removed (Yahoo shows NIFTYBEES at 13 instead of 129 on 19-20 Dec 2019)."""
    bars = C.fetch_bars(sym, "since:2012", "1d")
    good = []
    for i, b in enumerate(bars):
        near = sorted(x["c"] for x in bars[max(0, i - 3):i + 4] if x is not b)
        mid = near[len(near) // 2] if near else b["c"]
        if abs(b["c"] / mid - 1) < 0.4 and abs(b["o"] / mid - 1) < 0.4:
            good.append(b)
    return {b["t"].date(): b for b in good}


def backtest(rule, px, start, account):
    nifty, gold = px["NIFTYBEES.NS"], px["GOLDBEES.NS"]
    days = sorted(d for d in nifty if d >= start and d in gold)
    alld = sorted(nifty)
    idx = {d: i for i, d in enumerate(alld)}

    def back(src, d, n):                      # close n trading days before d (by Nifty calendar)
        i = idx[d] - n
        while i >= 0 and alld[i] not in src:
            i -= 1
        return src[alld[i]]["c"] if i >= 0 else None

    cash, held, units, fees, switches, curve = account, "cash", 0, 0.0, 0, []
    for i, d in enumerate(days[:-1]):
        nxt = days[i + 1]
        cash *= (1 + CASH_YIELD) ** (1 / 252) if held == "cash" else 1
        if rule == "hold":
            want = "nifty"
        elif d.month == nxt.month:
            want = held                                   # only decide at month end
        else:
            h200 = [nifty[alld[j]]["c"] for j in range(idx[d] - 199, idx[d] + 1) if alld[j] in nifty]
            above = len(h200) == 200 and h200[-1] > sum(h200) / 200
            n12 = nifty[d]["c"] / back(nifty, d, 252) - 1
            g12 = gold[d]["c"] / back(gold, d, 252) - 1
            if rule == "trend":
                want = "nifty" if above else "cash"
            else:
                want = "nifty" if above and n12 > CASH_YIELD else ("gold" if g12 > 0 else "cash")
        if want != held:
            if held != "cash":                            # sell at next open
                src = nifty if held == "nifty" else gold
                value = units * src[nxt]["o"]
                f = W.costs(0, value, True)
                cash, units, fees = value - f, 0, fees + f
            if want != "cash":                            # buy at next open
                src = nifty if want == "nifty" else gold
                p = src[nxt]["o"]
                units = int(cash / (p * 1.0012))
                f = W.costs(units * p, 0, False)
                cash -= units * p + f
                fees += f
            held, switches = want, switches + 1
        src = nifty if held == "nifty" else (gold if held == "gold" else None)
        curve.append((d, cash + (units * src[d]["c"] if src else 0)))
    return curve, fees, switches


def stats(curve, account):
    peak, dd = account, 0.0
    for _, v in curve:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak * 100)
    yrs = (curve[-1][0] - curve[0][0]).days / 365.25
    total = curve[-1][1] / account
    return (total - 1) * 100, (total ** (1 / yrs) - 1) * 100, dd


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--account", type=float, default=10000)
    a = p.parse_args(argv)
    px = {s: daily(s) for s in ("NIFTYBEES.NS", "GOLDBEES.NS")}
    start = dt.date.fromisoformat(a.start)
    print("ETF switching, %s, from %s (cash at %d%%/yr):" % (C.inr(a.account), a.start, CASH_YIELD * 100))
    for rule, label in (("hold", "Hold Nifty ETF"), ("trend", "Trend filter"), ("dual", "Dual momentum")):
        curve, fees, sw = backtest(rule, px, start, a.account)
        tot, cagr, dd = stats(curve, a.account)
        split = [v for d, v in curve if d < dt.date(2022, 1, 1)]
        pre = (split[-1] / a.account - 1) * 100 if split else 0
        post = (curve[-1][1] / split[-1] - 1) * 100 if split else tot
        print("  %-15s %s -> %s  total %+6.1f%%  per year %+5.1f%%  worst drop %4.1f%%  switches %3d  charges %s"
              "  | 2015-21 %+6.1f%%  2022-26 %+6.1f%%"
              % (label, C.inr(a.account), C.inr(curve[-1][1]), tot, cagr, dd, sw, C.inr(fees), pre, post))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
