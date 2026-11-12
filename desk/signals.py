"""Signal agent: posts an entry idea only when price, volume, news and the morning brief agree.

    python desk/signals.py [--at "YYYY-MM-DD HH:MM"]

An idea needs all of:
  a breakout whose trigger bar just closed (fresh, not stale),
  one of the day's top `in_play_top` breakouts by relative volume ("stocks in play"),
  price not already more than `chase_pct` past the trigger close,
  no bearish news on the stock in the last `news_veto_hours`,
  not on today's avoid list from the morning brief.
Bullish news is noted as support. Ideas go to the message board; nothing is traded.
"""
import argparse
import sys

import common as C
import predict as PR
import scan as S


def ideas(cfg, now):
    rows = []
    for s in cfg["watchlist"]:
        try:
            rows.append(S.orb_for(s, now, cfg))
        except C.DeskError:
            pass
    hits = sorted([r for r in rows if r.get("status") == "breakout"], key=lambda r: -(r.get("volume_x") or 0))
    in_play = {r["symbol"] for r in hits[:int(cfg["in_play_top"])]}
    brief = C.read_json("brief.json", {}) or {}
    avoid = {a["symbol"]: a.get("why", "") for a in brief.get("avoid", [])} if brief.get("date") == now.date().isoformat() else {}
    since = now - C.dt.timedelta(hours=cfg["news_veto_hours"])
    news = [e for e in C.events(now.date(), "news") if C.dt.datetime.fromisoformat(e["time"]) >= since]
    boundary = now.replace(minute=now.minute - now.minute % C.BAR_MIN, second=0)
    posted = {(e["symbol"], e.get("trigger")) for e in C.events(now.date(), "signal")}
    out = []
    for r in hits:
        end = C.dt.datetime.combine(now.date(), C.hhmm(r["trigger_bar"]), C.IST) + C.dt.timedelta(minutes=C.BAR_MIN)
        if end != boundary or (r["symbol"], r["trigger_bar"]) in posted:
            continue                                   # only the bar that just closed, and only once
        sym, why = r["symbol"], ["breakout %s on %sx volume" % (r["trigger_bar"], r["volume_x"])]
        if sym not in in_play:
            continue
        if r["last"] > r["trigger_close"] * (1 + cfg["chase_pct"] / 100):
            continue
        if sym in avoid:
            continue
        mine = [e for e in news if sym in e.get("stocks", [])]
        if any(e.get("bearish") for e in mine):
            continue
        bullish = any(e.get("bullish") for e in mine)
        if bullish:
            why.append("bullish news: " + next(e["text"] for e in mine if e.get("bullish"))[:80])
        f = PR.features(r, now, bullish)
        sc = PR.score(f)
        if cfg["predictor_min"] and sc < cfg["predictor_min"]:
            continue                                   # predictor filter, off in shadow mode
        out.append(C.post("signal", now, symbol=sym, entry=r["trigger_close"], or_low=r["or_low"], trigger=r["trigger_bar"],
                          or_high=r["or_high"], why="; ".join(why), score=sc, features=f))
    vetoed = set(avoid) | {s for e in news if e.get("bearish") for s in e.get("stocks", [])}
    out += reentries(rows, now, cfg, vetoed)
    return out


def reentries(rows, now, cfg, skip=()):
    """Re-entry watcher: after a profitable exit today, a new high above the exit price on strong volume."""
    out = []
    done = {e["symbol"] for e in C.events(now.date(), "signal") if e.get("reentry")}
    for e in C.events(now.date(), "exit"):
        sym = e["symbol"]
        if float(e.get("net") or 0) <= 0 or sym in done or sym in skip:
            continue
        r = next((x for x in rows if x.get("symbol") == sym and "last" in x), None)
        if not r:
            continue
        bars = C.closed_bars(C.by_day(C.fetch_bars(sym, "5d", "5m")).get(now.date(), []), now)
        after = [b for b in bars if b["t"] >= C.dt.datetime.fromisoformat(e["at"])]
        if len(after) >= 2 and after[-1]["c"] > max(float(e["price"]), max(b["h"] for b in after[:-1])) \
                and after[-1]["v"] >= cfg["volume_multiple"] * (sum(b["v"] for b in bars) / len(bars)):
            out.append(C.post("signal", now, symbol=sym, entry=round(after[-1]["c"], 2), or_low=r["or_low"],
                              or_high=r["or_high"], reentry=True,
                              why="re-entry: new high %s above the %s exit on strong volume" % (round(after[-1]["c"], 2), e["price"])))
            done.add(sym)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--at")
    a = p.parse_args(argv)
    found = ideas(C.load_config(), C.now_ist(a.at))
    for e in found:
        print("IDEA %s at ~%s: %s. Next: python desk/plan.py %s" % (e["symbol"], e["entry"], e["why"], e["symbol"]))
    if not found:
        print("No entry ideas right now.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
