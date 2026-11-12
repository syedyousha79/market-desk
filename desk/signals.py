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
import scan as S


def ideas(cfg, now):
    rows = []
    for s in cfg["watchlist"]:
        try:
            rows.append(S.orb_for(s, now, cfg))
        except C.DeskError:
            pass
    hits = sorted([r for r in rows if r.get("status") == "breakout"], key=lambda r: -(r.get("volume_x") or 0))
    in_play = {r["symbol"] for r in hits[:cfg["in_play_top"]]}
    brief = C.read_json("brief.json", {}) or {}
    avoid = {a["symbol"]: a.get("why", "") for a in brief.get("avoid", [])} if brief.get("date") == now.date().isoformat() else {}
    since = now - C.dt.timedelta(hours=cfg["news_veto_hours"])
    news = [e for e in C.events(now.date(), "news") if C.dt.datetime.fromisoformat(e["time"]) >= since]
    boundary = now.replace(minute=now.minute - now.minute % C.BAR_MIN, second=0)
    out = []
    for r in hits:
        end = C.dt.datetime.combine(now.date(), C.hhmm(r["trigger_bar"]), C.IST) + C.dt.timedelta(minutes=C.BAR_MIN)
        if end != boundary:
            continue                                   # only act on the bar that just closed
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
        if any(e.get("bullish") for e in mine):
            why.append("bullish news: " + next(e["text"] for e in mine if e.get("bullish"))[:80])
        out.append(C.post("signal", now, symbol=sym, entry=r["trigger_close"], or_low=r["or_low"],
                          or_high=r["or_high"], why="; ".join(why)))
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
