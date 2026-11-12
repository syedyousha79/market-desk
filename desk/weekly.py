"""Weekly momentum backtest on real daily prices (delivery trades, not intraday).

    python desk/weekly.py [--account 10000] [--years 5] [--hold 2] [--keep 4]

Rules (fixed in advance, not tuned):
  every Friday close, rank the watchlist by 3-month (63 trading days) return;
  only stocks above their 200-day average qualify, and only when Nifty is above its
  200-day average (otherwise hold cash); hold the top `hold`, equal money each, bought
  at Monday's open; keep a holding while it stays in the top `keep`, else sell Monday.
Charges: Zerodha delivery (no brokerage, STT 0.1% each side, stamp 0.015% on buys,
exchange 0.00307%, SEBI fee, GST, DP about Rs 15.93 per stock sold).
The watchlist is today's large caps, so the past looks better than it was (survivorship).
"""
import argparse
import sys

import common as C


def costs(buy, sell, sold):
    exch = (buy + sell) * 0.0000307
    sebi = (buy + sell) * 0.000001
    return buy * 0.001 + sell * 0.001 + buy * 0.00015 + exch + sebi + (exch + sebi) * 0.18 + (15.93 if sold else 0)


def series(sym, years):
    bars = C.fetch_bars(sym, "%dy" % (years + 1), "1d")
    return {b["t"].date(): b for b in bars}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", type=float, default=10000)
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--hold", type=int, default=2)
    p.add_argument("--keep", type=int, default=4)
    a = p.parse_args(argv)
    cfg = C.load_config()
    data = {}
    for s in cfg["watchlist"]:
        try:
            data[C.plain(C.yahoo_symbol(s))] = series(s, a.years)
        except C.DeskError as e:
            print("skipped %s: %s" % (s, e))
    nifty = series("^NSEI", a.years)
    days = sorted(nifty)
    start = days[-1].replace(year=days[-1].year - a.years)
    days = [d for d in days if d >= start]
    alld = sorted(nifty)

    def close_on(sym, d, back=0):
        src = nifty if sym == "^NSEI" else data[sym]
        hist = [x for x in alld if x <= d and x in src]
        return [src[x]["c"] for x in hist[-(back + 1):]] if back else (src[hist[-1]]["c"] if hist else None)

    cash, pos, log, fees = a.account, {}, [], 0.0           # pos: sym -> (shares, cost)
    fridays = [d for i, d in enumerate(days) if i + 1 < len(days) and days[i + 1].isocalendar()[1] != d.isocalendar()[1]]
    curve = []
    for f in fridays:
        nxt = next((d for d in days if d > f), None)
        if not nxt:
            break
        n200 = close_on("^NSEI", f, 199)
        risk_on = len(n200) == 200 and n200[-1] > sum(n200) / 200
        ranks = []
        for s, src in data.items():
            h = close_on(s, f, 199)
            if len(h) == 200 and h[-1] > sum(h) / 200:
                ranks.append(((h[-1] / h[-64] - 1) if len(h) > 64 else -9, s))
        ranks.sort(reverse=True)
        top = [s for _, s in ranks[:a.hold]] if risk_on else []
        keep = {s for _, s in ranks[:a.keep]} if risk_on else set()
        for s in list(pos):                                  # sells at Monday open
            if s not in keep and nxt in data[s]:
                sh, cost = pos.pop(s)
                px = data[s][nxt]["o"]
                fee = costs(0, sh * px, True)
                cash += sh * px - fee
                fees += fee
                log.append((nxt, "SELL", s, sh, px, sh * px - fee - cost))
        free = [s for s in top if s not in pos][: a.hold - len(pos)]
        for s in free:                                       # buys at Monday open, equal money
            if nxt not in data[s]:
                continue
            px = data[s][nxt]["o"]
            budget = cash / (a.hold - len(pos))
            sh = int(budget / (px * 1.0012))
            if sh < 1:
                continue
            fee = costs(sh * px, 0, False)
            cash -= sh * px + fee
            fees += fee
            pos[s] = (sh, sh * px + fee)
            log.append((nxt, "BUY", s, sh, px, 0))
        value = cash + sum(sh * close_on(s, f) for s, (sh, _) in pos.items())
        curve.append((f, value))

    final = curve[-1][1] if curve else a.account
    peak, dd = a.account, 0.0
    for _, v in curve:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak * 100)
    n0, n1 = close_on("^NSEI", curve[0][0]), close_on("^NSEI", curve[-1][0])
    sells = [x for x in log if x[1] == "SELL"]
    wins = sum(1 for x in sells if x[5] > 0)
    print("WEEKLY MOMENTUM on real daily prices: %s to %s (%d weeks), %d stocks, top %d held"
          % (curve[0][0], curve[-1][0], len(curve), len(data), a.hold))
    print("Paper account %s -> %s: %s (%+.1f%%), charges paid %s, worst drop from peak %.1f%%"
          % (C.inr(a.account), C.inr(final), C.inr(final - a.account), (final / a.account - 1) * 100, C.inr(fees), dd))
    print("Holding Nifty instead: %+.1f%%   |   closed trades %d, profitable %d (%.0f%%)"
          % ((n1 / n0 - 1) * 100, len(sells), wins, wins / len(sells) * 100 if sells else 0))
    print("\nBy year:")
    years = sorted({d.year for d, _ in curve})
    prev = a.account
    for y in years:
        end = [v for d, v in curve if d.year == y][-1]
        ny = [d for d, _ in curve if d.year == y]
        nret = (close_on("^NSEI", ny[-1]) / close_on("^NSEI", ny[0]) - 1) * 100
        print("  %d  strategy %+6.1f%%   Nifty %+6.1f%%" % (y, (end / prev - 1) * 100, nret))
        prev = end
    print("\nLast 6 trades:")
    for x in log[-6:]:
        print("  %s %-4s %-11s x%-3d @ %8.2f %s" % (x[0], x[1], x[2], x[3], x[4], ("P&L " + C.inr(x[5])) if x[1] == "SELL" else ""))
    print("\nPast results do not predict future ones. Today's large caps make the past look better (survivorship).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
