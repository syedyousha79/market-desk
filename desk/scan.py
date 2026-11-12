"""NSE scanner: opening-range breakouts and pre-open gaps on the watchlist.

    python desk/scan.py orb [--at "YYYY-MM-DD HH:MM"] [--symbols RELIANCE SBIN]
    python desk/scan.py gaps
    python desk/scan.py context            # Nifty, Bank Nifty, India VIX

Opening range = the first `or_minutes` (default 15: 09:15-09:30). A breakout is
the first closed 5-minute bar after the range that closes above the range high
(or below the low if allow_short) on volume at least `volume_multiple` times the
average 5-minute volume of the previous sessions. Writes trader/scan.json.
"""
import argparse
import sys

import common as C


def orb_for(symbol, now, cfg):
    bars = C.fetch_bars(symbol, "10d", "5m")
    days = C.by_day(bars)
    today = days.get(now.date())
    if not today:
        return {"symbol": C.plain(C.yahoo_symbol(symbol)), "status": "no bars today"}
    today = C.closed_bars(today, now)
    n_or = cfg["or_minutes"] // C.BAR_MIN
    if len(today) < n_or:
        return {"symbol": C.plain(C.yahoo_symbol(symbol)), "status": "opening range still forming"}
    rng = today[:n_or]
    hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
    # same time-of-day average from previous sessions: the open and close are always busier
    slot_vols = {}
    for d, bs in days.items():
        if d < now.date():
            for b in bs:
                slot_vols.setdefault(b["t"].strftime("%H:%M"), []).append(b["v"])
    prev = [v for vs in slot_vols.values() for v in vs]
    day_avg = (sum(prev) / len(prev)) if prev else 0

    def slot_avg(b):
        vs = slot_vols.get(b["t"].strftime("%H:%M"))
        return (sum(vs) / len(vs)) if vs else day_avg
    avg_v = day_avg
    out = {"symbol": C.plain(C.yahoo_symbol(symbol)), "or_high": round(hi, 2), "or_low": round(lo, 2),
           "or_mid": round((hi + lo) / 2, 2), "last": round(today[-1]["c"], 2), "avg_5m_volume": int(avg_v),
           "status": "inside range"}
    for b in today[n_or:]:
        ref = slot_avg(b)
        vol_ok = ref == 0 or b["v"] >= cfg["volume_multiple"] * ref
        side = "long" if b["c"] > hi else ("short" if (b["c"] < lo and cfg["allow_short"]) else None)
        if side and vol_ok:
            out.update(status="breakout", side=side, trigger_bar=b["t"].strftime("%H:%M"),
                       trigger_close=round(b["c"], 2), trigger_volume=int(b["v"]),
                       volume_x=round(b["v"] / ref, 1) if ref else None)
            break
        if side and not vol_ok and out["status"] == "inside range":
            out["status"] = "broke out on low volume (%s)" % b["t"].strftime("%H:%M")
    return out


def cmd_orb(args, cfg):
    now = C.now_ist(args.at)
    syms = args.symbols or cfg["watchlist"]
    rows, errors = [], []
    for s in syms:
        try:
            rows.append(orb_for(s, now, cfg))
        except C.DeskError as e:
            errors.append("%s: %s" % (s, e))
    hits = [r for r in rows if r.get("status") == "breakout"]
    C.write_json("scan.json", {"kind": "orb", "at": now.isoformat(), "hits": hits, "rows": rows, "errors": errors})
    print("Opening-range breakout scan, %s IST (range 09:15-%s), %d symbols, Yahoo 5-minute bars."
          % (now.strftime("%Y-%m-%d %H:%M"), (C.dt.datetime.combine(now.date(), C.OPEN)
              + C.dt.timedelta(minutes=cfg["or_minutes"])).strftime("%H:%M"), len(syms)))
    print("Market: %s" % C.phase(now, cfg))
    for r in rows:
        if r.get("status") == "breakout":
            print("  BREAKOUT %-11s %s at %s, close %s vs range %s-%s, volume %sx"
                  % (r["symbol"], r["side"], r["trigger_bar"], r["trigger_close"], r["or_low"], r["or_high"], r["volume_x"]))
    for r in rows:
        if r.get("status") != "breakout":
            print("  %-11s %s" % (r["symbol"], r["status"]))
    for e in errors:
        print("  skipped " + e)
    print("%d breakout(s). Written to trader/scan.json." % len(hits))


def cmd_gaps(args, cfg):
    now = C.now_ist(args.at)
    rows = []
    for s in args.symbols or cfg["watchlist"]:
        try:
            d = C.fetch_bars(s, "10d", "1d")
        except C.DeskError as e:
            print("  skipped %s: %s" % (s, e))
            continue
        past = [b for b in d if b["t"].date() < now.date()]
        today = [b for b in d if b["t"].date() == now.date()]
        if not past or not today:
            continue
        pc, op = past[-1]["c"], today[0]["o"]
        rows.append({"symbol": C.plain(C.yahoo_symbol(s)), "prev_close": round(pc, 2), "open": round(op, 2),
                     "gap_pct": round((op - pc) / pc * 100, 2)})
    rows.sort(key=lambda r: -abs(r["gap_pct"]))
    C.write_json("gaps.json", {"at": now.isoformat(), "rows": rows})
    print("Gaps at the open, %s IST:" % now.strftime("%Y-%m-%d %H:%M"))
    for r in rows:
        print("  %-11s %+6.2f%%  open %s, prev close %s" % (r["symbol"], r["gap_pct"], r["open"], r["prev_close"]))


def cmd_context(args, cfg):
    for s in cfg["context"]:
        try:
            d = C.fetch_bars(s, "5d", "1d")
            if len(d) >= 2:
                print("  %-10s %10.2f  %+.2f%%" % (s, d[-1]["c"], (d[-1]["c"] - d[-2]["c"]) / d[-2]["c"] * 100))
        except C.DeskError as e:
            print("  %-10s unavailable: %s" % (s, e))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("orb", "gaps", "context"):
        sp = sub.add_parser(name)
        sp.add_argument("--at", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
        sp.add_argument("--symbols", nargs="*")
    args = p.parse_args(argv)
    cfg = C.load_config()
    {"orb": cmd_orb, "gaps": cmd_gaps, "context": cmd_context}[args.cmd](args, cfg)


if __name__ == "__main__":
    try:
        main()
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
