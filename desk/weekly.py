"""Momentum backtest on real daily prices (delivery trades, not intraday).

    python desk/weekly.py --start 2015-01-01 --end 2021-12-31 [--every month] [--universe 50] [--hold 2]

Rules (fixed in advance, not tuned):
  on each rebalance day (last trading day of the week, or of the month with --every month)
  rank the stock list by 3-month (63 trading days) return; only stocks above their 200-day
  average qualify, and only when Nifty is above its 200-day average (otherwise cash);
  hold the top `hold` with equal money, bought at the next day's open; keep a holding while
  it stays in the top `keep` (default 2 x hold), else sell at the next open.
Charges: Zerodha delivery (no brokerage, STT 0.1% each side, stamp 0.015% on buys,
exchange 0.00307%, SEBI fee, GST, DP about Rs 15.93 per stock sold).
The stock lists are today's large caps, so the past looks better than it was (survivorship).
"""
import argparse
import datetime as dt
import sys

import common as C

# About 50 liquid NSE large caps (today's list; see the survivorship note above).
BIG50 = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "ITC",
         "BHARTIARTL", "MARUTI", "SUNPHARMA", "BAJFINANCE", "HINDUNILVR", "M&M", "TATASTEEL", "NTPC", "POWERGRID",
         "ULTRACEMCO", "TITAN", "ASIANPAINT", "NESTLEIND", "HCLTECH", "WIPRO", "TECHM", "ONGC", "COALINDIA",
         "ADANIPORTS", "ADANIENT", "BAJAJFINSV", "BAJAJ-AUTO", "HEROMOTOCO", "EICHERMOT", "DRREDDY", "CIPLA",
         "DIVISLAB", "APOLLOHOSP", "GRASIM", "JSWSTEEL", "HINDALCO", "INDUSINDBK", "BRITANNIA", "TATACONSUM",
         "SHRIRAMFIN", "BPCL", "SBILIFE", "HDFCLIFE", "TRENT", "BEL"]


def costs(buy, sell, sold):
    exch = (buy + sell) * 0.0000307
    sebi = (buy + sell) * 0.000001
    return buy * 0.001 + sell * 0.001 + buy * 0.00015 + exch + sebi + (exch + sebi) * 0.18 + (15.93 if sold else 0)


def closes(sym):
    """date -> bar, full history (needed for the 200-day average before the start date)."""
    return {b["t"].date(): b for b in C.fetch_bars(sym, "since:2013", "1d")}


def run(data, nifty, start, end, account, hold, keep, every):
    days = [d for d in sorted(nifty) if start <= d <= end]
    alld = sorted(nifty)
    idx = {d: i for i, d in enumerate(alld)}

    def hist(src, d, n):
        out, i = [], idx[d]
        while i >= 0 and len(out) < n:
            if alld[i] in src:
                out.append(src[alld[i]]["c"])
            i -= 1
        return out[::-1]

    def period_end(a, b):
        return a.isocalendar()[1] != b.isocalendar()[1] if every == "week" else a.month != b.month
    rebal = [d for i, d in enumerate(days) if i + 1 < len(days) and period_end(d, days[i + 1])]
    cash, pos, fees, curve, sells = account, {}, 0.0, [], []
    for f in rebal:
        nxt = days[days.index(f) + 1]
        n = hist(nifty, f, 200)
        risk_on = len(n) == 200 and n[-1] > sum(n) / 200
        ranks = []
        for s, src in data.items():
            h = hist(src, f, 200)
            if len(h) == 200 and h[-1] > sum(h) / 200:
                ranks.append((h[-1] / h[-64] - 1, s))
        ranks.sort(reverse=True)
        top = [s for _, s in ranks[:hold]] if risk_on else []
        keepset = {s for _, s in ranks[:keep]} if risk_on else set()
        for s in list(pos):
            if s not in keepset and nxt in data[s]:
                sh, cost = pos.pop(s)
                px = data[s][nxt]["o"]
                fee = costs(0, sh * px, True)
                cash += sh * px - fee
                fees += fee
                sells.append(sh * px - fee - cost)
        for s in [s for s in top if s not in pos][: hold - len(pos)]:
            if nxt not in data[s]:
                continue
            px = data[s][nxt]["o"]
            sh = int(cash / (hold - len(pos)) / (px * 1.0012))
            if sh < 1:
                continue
            fee = costs(sh * px, 0, False)
            cash -= sh * px + fee
            fees += fee
            pos[s] = (sh, sh * px + fee)
        curve.append((f, cash + sum(sh * hist(data[s], f, 1)[-1] for s, (sh, _) in pos.items())))
    return curve, fees, sells, hist


def report(title, curve, fees, sells, account, nifty, hist):
    final = curve[-1][1]
    peak, dd = account, 0.0
    for _, v in curve:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak * 100)
    npk, ndd = 0.0, 0.0
    for d, _ in curve:
        c = hist(nifty, d, 1)[-1]
        npk = max(npk, c)
        ndd = max(ndd, (npk - c) / npk * 100)
    nret = (hist(nifty, curve[-1][0], 1)[-1] / hist(nifty, curve[0][0], 1)[-1] - 1) * 100
    wins = sum(1 for x in sells if x > 0)
    print("%s %s to %s" % (title, curve[0][0], curve[-1][0]))
    print("  Strategy: %s -> %s (%+.1f%%), worst drop %.1f%%, charges %s, %d sales (%d profitable)"
          % (C.inr(account), C.inr(final), (final / account - 1) * 100, dd, C.inr(fees), len(sells), wins))
    print("  Holding Nifty: %+.1f%%, worst drop %.1f%%" % (nret, ndd))
    return (final / account - 1) * 100, nret, dd, ndd


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account", type=float, default=10000)
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default=dt.date.today().isoformat())
    p.add_argument("--every", choices=["week", "month"], default="week")
    p.add_argument("--universe", type=int, choices=[20, 50], default=20)
    p.add_argument("--hold", type=int, default=2)
    p.add_argument("--keep", type=int)
    p.add_argument("--exclude", nargs="*", default=[], help="drop names, e.g. ones that joined the index later")
    a = p.parse_args(argv)
    names = C.load_config()["watchlist"] if a.universe == 20 else BIG50
    data = {}
    for s in [n for n in names if n not in a.exclude]:
        try:
            data[C.plain(C.yahoo_symbol(s))] = closes(s)
        except C.DeskError as e:
            print("skipped %s: %s" % (s, str(e)[:60]))
    nifty = closes("^NSEI")
    curve, fees, sells, hist = run(data, nifty, dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end),
                                   a.account, a.hold, a.keep or 2 * a.hold, a.every)
    report("MOMENTUM, %d stocks, top %d, every %s:" % (len(data), a.hold, a.every), curve, fees, sells, a.account,
           nifty, hist)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
