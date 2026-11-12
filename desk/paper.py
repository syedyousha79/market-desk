"""Paper broker: turns a PASSed plan into a simulated trade, only on an explicit yes.

    python desk/paper.py ticket                      # the order in plain words, READY or not
    python desk/paper.py open --reply "yes"          # the user's reply, word for word
    python desk/paper.py update [--at ...]           # walk 5-minute bars: stop, target, flat-by
    python desk/paper.py close RELIANCE              # exit now at the last price
    python desk/paper.py status

Fills are simulated from Yahoo 5-minute bars: entry at the last closed bar's
close when you say yes; a bar that touches both stop and target counts as the
stop; a bar opening past the stop exits at that open; anything still open at
flat_by exits at that bar's open. Charges are Zerodha intraday rates from config.
This file never talks to a broker. Live orders are a separate, later step.
"""
import argparse
import re
import sys

import common as C
import journal as J


def ready(plan, cfg, now):
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


def explicit_yes(reply, symbol):
    r = reply.strip().lower().rstrip(".!")
    if "all" in r.split():
        return False
    other = re.findall(r"[a-z&]{2,}", r)
    allowed = {"yes", "buy", "sell", "place", "it", "go", "ahead", symbol.lower()}
    return bool(re.match(r"^(yes|y)\b", r)) and all(w in allowed for w in other)


def ticket(plan, cfg, now):
    ok, why = ready(plan, cfg, now)
    if not plan or not plan.get("qty"):
        return "No order to show: %s." % why
    lines = ["ORDER, %s account, Zerodha MIS (intraday)" % ("PAPER" if plan["paper"] else "LIVE"),
             "  %s %d %s at market (~%s)" % ("BUY" if plan["side"] == "long" else "SELL", plan["qty"], plan["symbol"], plan["entry"]),
             "  stop-loss %s, target %s, out by %s" % (plan["stop"], plan["target"], plan["flat_by"]),
             "  max loss about %s at the stop, charges included (a fast market can fill worse)" % C.inr(plan["max_loss"]),
             "  " + why]
    return "\n".join(lines)


def last_price(symbol, now):
    bars = C.closed_bars(C.fetch_bars(symbol, "1d", "5m"), now)
    if not bars:
        raise C.DeskError("no closed bar yet for %s" % symbol)
    return bars[-1]


def close_row(row, price, when, reason, cfg):
    qty = int(row["qty"])
    entry = float(row["entry"])
    long_ = row["side"] == "long"
    buy, sell = (entry, price) if long_ else (price, entry)
    gross = qty * (sell - buy)
    ch = C.charges(qty * buy, qty * sell, cfg)["total"]
    row.update(status="closed", exit_price=round(price, 2), exit_at=when, exit_reason=reason,
               gross_pnl=round(gross, 2), charges=ch, net_pnl=round(gross - ch, 2))
    return row


def walk(row, bars, cfg):
    """Return (price, 'HH:MM', reason) for the first exit in these bars, or None."""
    stop, target = float(row["stop"]), float(row["target"])
    flat = C.hhmm(row.get("flat_by") or cfg["flat_by"])
    long_ = row["side"] == "long"
    for b in bars:
        t = b["t"].strftime("%H:%M")
        if b["t"].time() >= flat:
            return b["o"], t, "flat-by"
        if long_:
            if b["o"] <= stop:
                return b["o"], t, "stop (gap)"
            if b["l"] <= stop:
                return stop, t, "stop"
            if b["h"] >= target:
                return (b["o"] if b["o"] >= target else target), t, "target"
        else:
            if b["o"] >= stop:
                return b["o"], t, "stop (gap)"
            if b["h"] >= stop:
                return stop, t, "stop"
            if b["l"] <= target:
                return (b["o"] if b["o"] <= target else target), t, "target"
    return None


def update(cfg, now):
    rows = J.trades()
    events = []
    for row in rows:
        if row["status"] != "open":
            continue
        bars = C.closed_bars(C.by_day(C.fetch_bars(row["symbol"], "5d", "5m")).get(
            C.dt.date.fromisoformat(row["date"]), []), now)
        opened = C.hhmm(row["opened_at"])
        after = [b for b in bars if b["t"].time() >= opened]
        hit = walk(row, after, cfg)
        if hit is None and now.date().isoformat() > row["date"]:
            hit = (bars[-1]["c"], bars[-1]["t"].strftime("%H:%M"), "session end") if bars else None
        if hit:
            close_row(row, hit[0], hit[1], hit[2], cfg)
            events.append(row)
    if events:
        J.save(rows)
    return events


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
    cfg = C.load_config()
    now = C.now_ist(a.at)
    plan = C.read_json("plan.json")

    if a.cmd == "ticket":
        print(ticket(plan, cfg, now))
    elif a.cmd == "open":
        ok, why = ready(plan, cfg, now)
        if not ok:
            raise C.DeskError("not placed: " + why)
        if not plan["paper"]:
            raise C.DeskError("config is set to live; this paper broker only simulates. Live orders go through Kite with their own approval.")
        if not explicit_yes(a.reply, plan["symbol"]):
            print("Not recorded: %r is not an explicit yes for this one order. Ask again." % a.reply)
            return 3
        rows = J.trades()
        if any(r["status"] == "open" and r["symbol"] == plan["symbol"] for r in rows):
            raise C.DeskError("%s already has an open paper trade" % plan["symbol"])
        bar = last_price(plan["symbol"], now)
        px = bar["c"]
        long_ = plan["side"] == "long"
        if (long_ and px <= plan["stop"]) or (not long_ and px >= plan["stop"]):
            raise C.DeskError("price %s is already past the stop %s; plan again" % (px, plan["stop"]))
        qty = plan["qty"]
        buy, sell = (px, plan["stop"]) if long_ else (plan["stop"], px)
        max_loss = qty * abs(px - plan["stop"]) + C.charges(qty * buy, qty * sell, cfg)["total"]
        row = {"id": "%s-%s-%d" % (plan["date"], plan["symbol"], len(rows) + 1), "date": plan["date"],
               "symbol": plan["symbol"], "side": plan["side"], "qty": qty, "entry": round(px, 2),
               "stop": plan["stop"], "target": plan["target"], "max_loss": round(max_loss, 2),
               "opened_at": (bar["t"] + C.dt.timedelta(minutes=C.BAR_MIN)).strftime("%H:%M"),
               "status": "open", "setup": plan.get("setup"), "paper": "true", "reply": a.reply}
        rows.append(row)
        J.save(rows)
        plan["risk"]["verdict"] = "USED"
        C.write_json("plan.json", plan)
        msg = "PAPER %s %d %s @ %s | stop %s | target %s | max loss %s" % (
            "BUY" if long_ else "SELL", qty, plan["symbol"], row["entry"], plan["stop"], plan["target"], C.inr(max_loss))
        print("Recorded your yes. " + msg)
        C.telegram_send(msg)
    elif a.cmd == "update":
        ev = update(cfg, now)
        for r in ev:
            msg = "PAPER EXIT %s %s @ %s (%s) | net %s after %s charges" % (
                r["symbol"], r["side"], r["exit_price"], r["exit_reason"], C.inr(float(r["net_pnl"])), C.inr(float(r["charges"])))
            print(msg)
            C.telegram_send(msg)
        if not ev:
            print("No exits.")
    elif a.cmd == "close":
        rows = J.trades()
        sym = C.plain(C.yahoo_symbol(a.symbol))
        for r in rows:
            if r["status"] == "open" and r["symbol"] == sym:
                bar = last_price(sym, now)
                close_row(r, bar["c"], now.strftime("%H:%M"), "manual", cfg)
                J.save(rows)
                print("Closed %s @ %s, net %s" % (sym, r["exit_price"], C.inr(float(r["net_pnl"]))))
                return 0
        raise C.DeskError("no open paper trade in %s" % sym)
    elif a.cmd == "status":
        s = J.summary()
        print("Open %d | closed %d | win rate %s%% | net %s after %s charges"
              % (s["open"], s["trades"], s["win_rate"] if s["win_rate"] is not None else "-", C.inr(s["net_pnl"]), C.inr(s["charges"])))
        for r in J.trades():
            if r["status"] == "open":
                print("  OPEN %s %s %s @ %s stop %s target %s" % (r["symbol"], r["side"], r["qty"], r["entry"], r["stop"], r["target"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
