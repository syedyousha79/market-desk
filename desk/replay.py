"""Replay recent real NSE sessions through the full desk, bar by bar, on a fake account.

    python desk/replay.py --usd 50 --days 5        # $50 converted at the live USD/INR rate
    python desk/replay.py --account 1000 --days 10

Every 5 minutes of each past session it does what you would: scan, and when a
fresh breakout closes, plan it, run the risk gate, and say a simulated "yes" if
it passes; open trades are walked against the real bars for stop, target and
flat-by. Uses Yahoo 5-minute bars (up to about 60 days back).

Runs in its own folder (trader/replay/), never touches your real journal, and
never sends Telegram. Past results do not predict future ones.
"""
import argparse
import datetime as dt
import io
import os
import shutil
import sys
from contextlib import redirect_stdout

import common as C


def usd_inr():
    bars = C.fetch_bars("INR=X", "5d", "1d")
    if not bars:
        raise C.DeskError("no USD/INR rate from Yahoo")
    return bars[-1]["c"]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--usd", type=float, help="fake account in US dollars, converted at the live rate")
    g.add_argument("--account", type=float, help="fake account in rupees")
    p.add_argument("--days", type=int, default=5)
    p.add_argument("--symbols", nargs="*")
    a = p.parse_args(argv)

    os.environ["NSE_NO_TELEGRAM"] = "1"
    real_cfg = C.load_config()
    rate = None
    if a.usd:
        rate = usd_inr()
        account = round(a.usd * rate, 2)
    else:
        account = a.account or real_cfg["account_inr"]

    # isolated workspace
    work = os.path.join(C.TRADER, "replay")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    cfg_over = {k: v for k, v in real_cfg.items() if k not in ("charges",)}
    cfg_over.update(account_inr=account, paper=True, telegram_paused=True)
    if a.symbols:
        cfg_over["watchlist"] = a.symbols
    C.TRADER = work
    C.write_json("config.json", cfg_over)

    # cache each symbol's bars once (one download per symbol, not one per step)
    real_fetch, cache = C.fetch_bars, {}

    def cached(symbol, rng="5d", interval="5m"):
        k = (C.yahoo_symbol(symbol), interval)
        if k not in cache:
            cache[k] = real_fetch(symbol, "60d" if interval == "5m" else "3mo", interval)
        return cache[k]
    C.fetch_bars = cached

    import journal as J
    import paper as P
    import plan as PL
    import risk as R
    import predict as PR
    import signals as SG
    labelled = []

    cfg = C.with_money(C.load_config())
    days = sorted(C.by_day(cached("^NSEI")).keys())
    days = [d for d in days if C.is_trading_day(d, cfg)][-a.days:]
    blocks = {}
    signals = 0

    def quiet(fn, args):
        with redirect_stdout(io.StringIO()) as buf:
            try:
                code = fn(args)
            except C.DeskError as e:
                return "error", str(e)
        return code, buf.getvalue()

    for day in days:
        t = dt.datetime.combine(day, dt.time(9, 35), C.IST)
        end = dt.datetime.combine(day, C.CLOSE, C.IST)
        while t <= end:
            stamp = t.strftime("%Y-%m-%d %H:%M")
            P.update(cfg, t)
            for h in SG.ideas(cfg, t):
                signals += 1
                if h.get("features"):                  # label every idea for the predictor, traded or not
                    future = [b for b in C.by_day(cached(h["symbol"])).get(day, []) if b["t"] >= t]
                    stop = (h["or_low"] + h["or_high"]) / 2
                    labelled.append(dict(h["features"], date=day.isoformat(), symbol=h["symbol"], score=h["score"],
                                         label=PR.label(h["entry"], stop, future, C.hhmm(cfg["flat_by"]))))
                C.write_json("scan.json", {"hits": [dict(h, trigger_close=h["entry"], side="long",
                                                         or_mid=round((h["or_low"] + h["or_high"]) / 2, 2))]})
                code, out = quiet(PL.main, [h["symbol"], "--at", stamp])
                if code == "error" or "No trade" in out:
                    blocks["too expensive for the loss limit"] = blocks.get("too expensive for the loss limit", 0) + 1
                    continue
                code, out = quiet(R.main, ["--at", stamp])
                if code != 0:
                    for ln in out.splitlines()[1:]:
                        reason = ln.strip("- ").split(" (")[0]
                        reason = "already traded today" if "trade(s) today" in reason else reason
                        reason = "outside entry window" if "entry window" in reason else reason
                        reason = "today's loss budget used up by the open trade" if "today's loss budget" in reason else reason
                        reason = "net reward:risk under the minimum after charges" if "reward:risk" in reason else reason
                        blocks[reason] = blocks.get(reason, 0) + 1
                    continue
                quiet(P.main, ["open", "--reply", "yes", "--at", stamp])
            t += dt.timedelta(minutes=C.BAR_MIN)
        P.update(cfg, end + dt.timedelta(minutes=10))

    if labelled:
        import csv
        path = C.state_path("ideas.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(labelled[0].keys()))
            w.writeheader()
            w.writerows(labelled)
        won = sum(r["label"] for r in labelled)
        hi = [r for r in labelled if r["score"] >= 55]
        print("Predictor (shadow): %d ideas labelled, %.0f%% reached +1R first; ideas scoring 55+: %s"
              % (len(labelled), won / len(labelled) * 100,
                 "%.0f%% of %d" % (sum(r["label"] for r in hi) / len(hi) * 100, len(hi)) if hi else "none"))
    rows = J.trades()
    s = J.summary(rows)
    print("REPLAY on real NSE 5-minute data: %s to %s (%d sessions), %d symbols"
          % (days[0], days[-1], len(days), len(cfg["watchlist"])))
    if rate:
        print("Fake account: $%g = %s at USD/INR %.2f (live)" % (a.usd, C.inr(account), rate))
    else:
        print("Fake account: %s" % C.inr(account))
    print("Rules: risk %s%%/trade, stop at -%s%%/day, -%s%%/week, kill at -%s%% from peak, %d trade/day, entries %s-%s, out by %s"
          % (cfg["risk_pct_trade"], cfg["loss_pct_day"], cfg["loss_pct_week"], cfg["kill_drawdown_pct"],
             cfg["max_trades_per_day"], cfg["no_entries_before"], cfg["no_entries_after"], cfg["flat_by"]))
    print()
    for r in rows:
        print("  %s %-10s %s x%-3s in %-8s %s  out %-8s %s %-11s net %s"
              % (r["date"], r["symbol"], r["side"], r["qty"], r["entry"], r["opened_at"],
                 r["exit_price"], r["exit_at"], r["exit_reason"], C.inr(float(r["net_pnl"] or 0))))
    if not rows:
        print("  no trades taken")
    print()
    end_bal = account + s["net_pnl"]
    print("Entry ideas %d, trades taken %d, wins %d, losses %d"
          % (signals, s["trades"], s["wins"], s["trades"] - s["wins"]))
    print("Net P&L %s after %s charges -> account %s (%+.2f%%)"
          % (C.inr(s["net_pnl"]), C.inr(s["charges"]), C.inr(end_bal), s["net_pnl"] / account * 100))
    if rate:
        print("In dollars: $%.2f -> $%.2f" % (a.usd, end_bal / rate))
    if blocks:
        print("Signals not taken, by reason:")
        for k, v in sorted(blocks.items(), key=lambda kv: -kv[1]):
            print("  %3d  %s" % (v, k))
    print("\nReplay journal: trader/replay/journal.csv. Simulated fills; real ones can be worse.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
