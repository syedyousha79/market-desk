"""Three combined strategies (designed by Fable 5.2 before seeing data) vs holding Nifty, on real daily prices.

    python desk/combo.py [--account 10000] [--months 12]

  tilt   : month end, 70% in whichever of NIFTYBEES/GOLDBEES has the higher 12-month return, 30% in the
           other; trade only when a weight is more than 10 points off target; never cash
  twokey : Friday, hold NIFTYBEES unless Nifty < 200-day average AND India VIX > its 252-day median (then cash)
  sectors: month end, top 2 of 8 sector indices by 12-1 month return, keep while in the top 4; if Nifty is
           below its 200-day average hold NIFTYBEES instead (sector indices traded at +0.3% extra per trade)
  hold   : NIFTYBEES bought once;  mix5050: 50/50 Nifty/gold, rebalanced when 10 points off
Cash earns 6%/yr. Charges: Zerodha delivery (weekly.costs) incl. Rs 15.93 DP per sale. Indices are price-only.
"""
import argparse
import datetime as dt
import sys

import common as C
import etf as E
import weekly as W

SECTORS = ["^CNXIT", "^CNXPHARMA", "^CNXAUTO", "^CNXFMCG", "^CNXMETAL", "^CNXENERGY", "^CNXREALTY", "^CNXINFRA"]


def simulate(name, px, start, account, months):
    N, G, V = px["NIFTYBEES.NS"], px["GOLDBEES.NS"], px["^INDIAVIX"]
    nifty = px["^NSEI"]
    days = sorted(d for d in N if d >= start and d in G and d in nifty)
    cal = sorted(nifty)
    pos = {d: i for i, d in enumerate(cal)}
    L = months * 21

    def close(sym, d, back=0):
        src = px[sym]
        i = pos.get(d, None)
        if i is None:
            return src[d]["c"] if d in src else None
        i -= back
        while i >= 0 and cal[i] not in src:
            i -= 1
        return src[cal[i]]["c"] if i >= 0 else None

    def above200(d):
        i = pos[d]
        h = [nifty[cal[j]]["c"] for j in range(max(0, i - 199), i + 1)]
        return len(h) == 200 and h[-1] > sum(h) / 200

    units, cash, fees, trades, curve, prev_target = {}, account, 0.0, 0, [], None
    for k, d in enumerate(days[:-1]):
        nxt = days[k + 1]
        cash *= (1 + E.CASH_YIELD) ** (1 / 252)
        month_end, week_end = d.month != nxt.month, d.isocalendar()[1] != nxt.isocalendar()[1]
        value = cash + sum(u * close(s, d) for s, u in units.items())
        target = None
        if name == "hold" and k == 0:
            target = {"NIFTYBEES.NS": 1.0}
        elif name in ("tilt", "mix5050") and (month_end or k == 0):
            if name == "mix5050":
                target = {"NIFTYBEES.NS": 0.5, "GOLDBEES.NS": 0.5}
            else:
                rn, rg = close("NIFTYBEES.NS", d) / close("NIFTYBEES.NS", d, L), close("GOLDBEES.NS", d) / close("GOLDBEES.NS", d, L)
                target = {"NIFTYBEES.NS": 0.7, "GOLDBEES.NS": 0.3} if rn >= rg else {"NIFTYBEES.NS": 0.3, "GOLDBEES.NS": 0.7}
            now = {s: units.get(s, 0) * close(s, d) / value for s in target}
            if k and max(abs(now[s] - target[s]) for s in target) <= 0.10:
                target = None                                       # inside the band: no trade
        elif name == "twokey" and (week_end or k == 0):
            vix = [V[cal[j]]["c"] for j in range(max(0, pos[d] - 251), pos[d] + 1) if cal[j] in V]
            fear = vix and close("^INDIAVIX", d) is not None and close("^INDIAVIX", d) > sorted(vix)[len(vix) // 2]
            target = {} if (not above200(d) and fear) else {"NIFTYBEES.NS": 1.0}
        elif name == "sectors" and (month_end or k == 0):
            if not above200(d):
                target = {"NIFTYBEES.NS": 1.0}
            else:
                ranked = sorted(((close(s, d, 21) / close(s, d, L + 21), s) for s in SECTORS
                                 if close(s, d, L + 21)), reverse=True)
                top4 = [s for _, s in ranked[:4]]
                keep = [s for s in units if s in top4][:2]
                pick = keep + [s for _, s in ranked if s not in keep][: 2 - len(keep)]
                target = {s: 0.5 for s in pick}
        if target is None or target == prev_target and name in ("hold", "twokey", "sectors"):
            curve.append((d, value))
            continue
        prev_target = target
        for s in list(units):                                       # sell what is above target, at next open
            p = px[s][nxt]["o"] if nxt in px[s] else close(s, d)
            want = target.get(s, 0) * value
            have = units[s] * p
            if have > want + 1:
                sell = have - want
                f = W.costs(0, sell, True) + (sell * 0.003 if s in SECTORS else 0)
                units[s] -= sell / p
                cash += sell - f
                fees, trades = fees + f, trades + 1
                if units[s] * p < 1:
                    units.pop(s)
        for s, w in target.items():                                 # buy what is below target
            p = px[s][nxt]["o"] if nxt in px[s] else close(s, d)
            need = w * value - units.get(s, 0) * p
            if need > 1 and cash > 1:
                buy = min(need, cash / 1.0016)
                f = W.costs(buy, 0, False) + (buy * 0.003 if s in SECTORS else 0)
                units[s] = units.get(s, 0) + buy / p
                cash -= buy + f
                fees, trades = fees + f, trades + 1
        curve.append((d, cash + sum(u * close(s, d) for s, u in units.items())))
    return curve, fees, trades


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", type=float, default=10000)
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--start", default="2013-02-01")
    a = p.parse_args(argv)
    px = {s: E.daily(s) for s in ["NIFTYBEES.NS", "GOLDBEES.NS", "^NSEI", "^INDIAVIX"] + SECTORS}
    start, split = dt.date.fromisoformat(a.start), dt.date(2020, 1, 1)
    print("%s, %d-month lookback, from %s (split at 2020):" % (C.inr(a.account), a.months, a.start))
    for name in ("hold", "mix5050", "tilt", "twokey", "sectors"):
        curve, fees, trades = simulate(name, px, start, a.account, a.months)
        tot, cagr, dd = E.stats(curve, a.account)
        pre = [v for d, v in curve if d < split]
        print("  %-8s -> %s  total %+6.1f%%  per year %+5.1f%%  worst drop %4.1f%%  trades %3d  charges %-9s"
              " | 2013-19 %+6.1f%%  2020-26 %+6.1f%%"
              % (name, C.inr(curve[-1][1]), tot, cagr, dd, trades, C.inr(fees),
                 (pre[-1] / a.account - 1) * 100, (curve[-1][1] / pre[-1] - 1) * 100))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
