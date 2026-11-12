"""Paper broker and position monitor. Entries need your explicit yes; exits never do.

    python desk/paper.py ticket                     # the order in plain words
    python desk/paper.py open --reply "yes"         # your reply, word for word
    python desk/paper.py update [--at ...]          # monitor open trades (the loop runs this)
    python desk/paper.py close SYMBOL               # exit now at the last price
    python desk/paper.py status

Exit rules, checked every bar in this order (money first, then profit):
  stop or trailing stop hit -> out | bad news on the stock -> out |
  prices stopped arriving -> out | no +0.5R after 30 min -> out | 15:15 -> out.
At +1R the stop moves to break-even, then trails 1R behind the best price, so
profit can run while the stop only moves up. Fills are simulated from 5-minute bars.
"""
import argparse
import re
import sys

import common as C
import journal as J


def ready(plan, now):
    if not plan or not plan.get("qty"):
        return False, "no sized plan"
    risk = plan.get("risk") or {}
    if risk.get("verdict") != "PASS":
        return False, "risk check has not passed (run risk.py)"
    if risk.get("fingerprint") != C.fingerprint(plan):
        return False, "the plan changed after the risk check; run risk.py again"
    if plan["date"] != now.date().isoformat():
        return False, "the plan is not from today"
    return True, "READY: risk PASS, waiting for your yes"


def explicit_yes(reply, symbol, qty=None):
    """True only for a plain yes to this one order. A number in the reply must be the planned quantity."""
    r = reply.strip().lower().rstrip(".!")
    if "all" in r.split() or any(qty is None or int(n) != int(qty) for n in re.findall(r"\d+", r)):
        return False
    allowed = {"yes", "buy", "sell", "place", "it", "go", "ahead", "shares", symbol.lower()}
    return bool(re.match(r"^(yes|y)\b", r)) and all(w in allowed for w in re.findall(r"[a-z&]{2,}", r))


def walk(row, bars, cfg, news_times=()):
    """Monitor bars after entry. Returns (exit or None, state) with exit = (price, 'HH:MM', reason)."""
    entry, stop0 = float(row["entry"]), float(row["stop"])
    s = 1 if row["side"] == "long" else -1
    R = abs(entry - stop0)
    stop, peak, prev, partial = stop0, entry, None, None
    flat = C.hhmm(row.get("flat_by") or cfg["flat_by"])
    news = list(news_times)

    def st():
        return {"stop": round(stop, 2), "peak": round(peak, 2), "partial": partial}
    for i, b in enumerate(bars):
        t = b["t"].strftime("%H:%M")
        state = st()
        if b["t"].time() >= flat:
            if b["t"].time() > flat and prev:            # feed skipped the flat-by bar
                return (prev["c"], flat.strftime("%H:%M"), "flat-by (data gap)"), state
            return (b["o"], t, "flat-by"), state
        if news and b["t"] >= news[0]:
            return (b["o"], t, "bad news"), state
        label = "stop" if stop == stop0 else ("break-even stop" if stop == entry else "trailing stop")
        if (b["o"] - stop) * s <= 0:
            return (b["o"], t, label + " (gap)"), state
        if ((b["l"] if s == 1 else b["h"]) - stop) * s <= 0:
            return (stop, t, label), state
        peak = max(peak, b["h"]) if s == 1 else min(peak, b["l"])
        gain = (peak - entry) * s
        if partial is None and int(row["qty"]) >= 2 and gain >= cfg["partial_r"] * R:
            level = entry + s * cfg["partial_r"] * R              # sell half at +2R (at the open if it gapped past)
            partial = (round(b["o"] if (b["o"] - level) * s >= 0 else level, 2), t)
        if gain >= cfg["breakeven_r"] * R:
            new = max(entry * s, (peak - s * cfg["trail_r"] * R) * s) * s
            stop = max(stop, new) if s == 1 else min(stop, new)
        if (i + 1) * C.BAR_MIN == cfg["time_stop_min"] and gain < cfg["time_stop_r"] * R:
            return (b["c"], t, "time stop"), st()
        prev = b
    return None, st()


def close_row(row, price, when, reason, cfg):
    """Close the trade; a half already sold at +2R counts as its own order."""
    qty, entry, s = int(row["qty"]), float(row["entry"]), 1 if row["side"] == "long" else -1
    pq = int(row.get("partial_qty") or 0)
    legs = [(pq, float(row["partial_price"]))] if pq else []
    legs.append((qty - pq, price))
    gross = sum(q * (px - entry) * s for q, px in legs)
    ch = 0.0
    for n, (q, px) in enumerate(legs):                    # entry charged once, each exit order separately
        opening = qty * entry if n == 0 else 0
        ch += C.charges(opening if s == 1 else q * px, q * px if s == 1 else opening, cfg)["total"]
    row.update(status="closed", exit_price=round(price, 2), exit_at=when, exit_reason=reason,
               gross_pnl=round(gross, 2), charges=round(ch, 2), net_pnl=round(gross - ch, 2))


def update(cfg, now):
    """One monitor pass: exits and stop raises, each posted to the message board."""
    rows, events = J.trades(), []
    for row in rows:
        if row["status"] != "open":
            continue
        day = C.dt.date.fromisoformat(row["date"])
        bars = C.closed_bars(C.by_day(C.fetch_bars(row["symbol"], "5d", "5m")).get(day, []), now)
        opened = C.dt.datetime.combine(day, C.hhmm(row["opened_at"]), C.IST)
        after = [b for b in bars if b["t"] >= opened]
        hit, state = walk(row, after, cfg, C.bad_news(row["symbol"], day, opened))
        if hit is None and bars and C.phase(now, cfg) == "open" and \
                now - (bars[-1]["t"] + C.dt.timedelta(minutes=C.BAR_MIN)) > C.dt.timedelta(minutes=cfg["stale_data_min"]):
            hit = (bars[-1]["c"], now.strftime("%H:%M"), "prices stopped arriving")
        if hit is None and now.date() > day and bars:
            hit = (bars[-1]["c"], bars[-1]["t"].strftime("%H:%M"), "session end")
        if state["partial"] and not row.get("partial_qty"):
            row.update(partial_qty=int(row["qty"]) // 2, partial_price=state["partial"][0], partial_at=state["partial"][1])
            events.append(C.post("partial", now, symbol=row["symbol"], qty=row["partial_qty"], price=row["partial_price"]))
        if hit:
            close_row(row, *hit, cfg)
            events.append(C.post("exit", now, symbol=row["symbol"], price=row["exit_price"], reason=row["exit_reason"],
                                 net=row["net_pnl"], charges=row["charges"]))
        elif state["stop"] != float(row.get("cur_stop") or row["stop"]):
            row["cur_stop"] = state["stop"]
            events.append(C.post("trail", now, symbol=row["symbol"], stop=state["stop"], peak=state["peak"]))
    if events:
        J.save(rows)
    return events


def last_bar(symbol, now):
    bars = C.closed_bars(C.fetch_bars(symbol, "1d", "5m"), now)
    if not bars:
        raise C.DeskError("no closed bar yet for %s" % symbol)
    return bars[-1]


def open_trade(plan, reply, cfg, now):
    ok, why = ready(plan, now)
    if not ok:
        raise C.DeskError("not placed: " + why)
    if not plan["paper"]:
        raise C.DeskError("config is live; this broker only simulates. Live orders go through Kite with their own approval.")
    if not explicit_yes(reply, plan["symbol"], plan["qty"]):
        return None
    made = C.dt.datetime.combine(now.date(), C.hhmm(plan["made_at"]), C.IST)
    if C.bad_news(plan["symbol"], now.date(), made):           # re-check: news changed after the plan
        raise C.DeskError("bad news on %s since the plan; not placed. Plan again if you still want it." % plan["symbol"])
    rows = J.trades()
    if any(r["status"] == "open" and r["symbol"] == plan["symbol"] for r in rows):
        raise C.DeskError("%s already has an open paper trade" % plan["symbol"])
    bar = last_bar(plan["symbol"], now)
    px, long_ = bar["c"], plan["side"] == "long"
    if (px - plan["stop"]) * (1 if long_ else -1) <= 0:
        raise C.DeskError("price %s is already past the stop %s; plan again" % (px, plan["stop"]))
    qty = plan["qty"]
    buy, sell = (px, plan["stop"]) if long_ else (plan["stop"], px)
    row = {"id": "%s-%s-%d" % (plan["date"], plan["symbol"], len(rows) + 1), "date": plan["date"],
           "symbol": plan["symbol"], "side": plan["side"], "qty": qty, "entry": round(px, 2),
           "stop": plan["stop"], "cur_stop": plan["stop"], "target": plan["target"],
           "max_loss": round(qty * abs(px - plan["stop"]) + C.charges(qty * buy, qty * sell, cfg)["total"], 2),
           "opened_at": (bar["t"] + C.dt.timedelta(minutes=C.BAR_MIN)).strftime("%H:%M"),
           "status": "open", "setup": plan.get("setup"), "paper": "true", "reply": reply}
    J.save(rows + [row])
    plan["risk"]["verdict"] = "USED"
    C.write_json("plan.json", plan)
    C.post("entry", now, symbol=row["symbol"], qty=qty, price=row["entry"], stop=row["stop"], max_loss=row["max_loss"])
    return row


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for n in ("ticket", "update", "status"):
        sub.add_parser(n).add_argument("--at")
    o = sub.add_parser("open")
    o.add_argument("--reply", required=True)
    o.add_argument("--at")
    c = sub.add_parser("close")
    c.add_argument("symbol")
    c.add_argument("--at")
    a = p.parse_args(argv)
    cfg, now, plan = C.load_config(), C.now_ist(a.at), C.read_json("plan.json")

    if a.cmd == "ticket":
        ok, why = ready(plan, now)
        if not plan or not plan.get("qty"):
            print("No order to show: %s." % why)
        else:
            print("ORDER, %s account, Zerodha MIS (intraday)\n  BUY %d %s at market (~%s)\n  stop-loss %s, then"
                  " break-even at +1R and a trailing stop; out by %s\n  max loss about %s at the stop, charges"
                  " included (a fast market can fill worse)\n  %s"
                  % ("PAPER" if plan["paper"] else "LIVE", plan["qty"], plan["symbol"], plan["entry"], plan["stop"],
                     plan["flat_by"], C.inr(plan["max_loss"]), why))
    elif a.cmd == "open":
        row = open_trade(plan, a.reply, cfg, now)
        if row is None:
            print("Not recorded: %r is not an explicit yes for this one order. Ask again." % a.reply)
            return 3
        print("Recorded your yes. PAPER BUY %s %s @ %s, stop %s, max loss %s"
              % (row["qty"], row["symbol"], row["entry"], row["stop"], C.inr(row["max_loss"])))
    elif a.cmd == "update":
        ev = update(cfg, now)
        for e in ev:
            print(e)
        if not ev:
            print("No changes.")
    elif a.cmd == "close":
        rows, sym = J.trades(), C.plain(C.yahoo_symbol(a.symbol))
        for r in rows:
            if r["status"] == "open" and r["symbol"] == sym:
                close_row(r, last_bar(sym, now)["c"], now.strftime("%H:%M"), "manual", cfg)
                J.save(rows)
                C.post("exit", now, symbol=sym, price=r["exit_price"], reason="manual", net=r["net_pnl"], charges=r["charges"])
                print("Closed %s @ %s, net %s" % (sym, r["exit_price"], C.inr(float(r["net_pnl"]))))
                return 0
        raise C.DeskError("no open paper trade in %s" % sym)
    else:
        s = J.summary()
        print("Open %d | closed %d | win rate %s%% | net %s after %s charges"
              % (s["open"], s["trades"], s["win_rate"] if s["win_rate"] is not None else "-", C.inr(s["net_pnl"]), C.inr(s["charges"])))
        for r in J.trades():
            if r["status"] == "open":
                print("  OPEN %s %s @ %s, stop now %s" % (r["qty"], r["symbol"], r["entry"], r.get("cur_stop") or r["stop"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
