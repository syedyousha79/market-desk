"""Approver and guardian: say yes/no to ideas instead of a person, and pull the emergency stop.

Paper mode only: the approver refuses to act if `paper` is false. It says yes only when
the plan sizes to at least 1 share and the risk gate passes (limits, entry window, open
trades, losing streak, kill switch). Every yes and no goes to the message board with its reason.

The guardian runs first on every pass and halts trading by itself:
  trader/STOP file exists          -> close all, halt until reviewed
  equity 15% below its peak        -> close all, halt until reviewed (kill switch)
  today's loss (incl. open) hits the day limit -> close all, halt for today
  losses in a row hit the limit    -> halt until reviewed
  India VIX up sharply today       -> no new trades today
  3 unexpected errors today        -> close all, halt until reviewed
`python desk/brain.py resume` lifts a halt after you have looked at what happened.
"""
import os

import common as C
import journal as J
import paper as P
import plan as PL
import risk as R


# ---------- guardian ----------

def halted(now):
    h = C.read_json("state/halt.json")
    if not h:
        return None
    return h if h["until"] == "review" or h["day"] == now.date().isoformat() else None


def halt(now, reason, until, close=True, cfg=None):
    if close:
        close_all(now, "guardian: " + reason, cfg)
    C.write_json("state/halt.json", {"day": now.date().isoformat(), "reason": reason, "until": until})
    C.post("halt", now, reason=reason, until=until)


def close_all(now, reason, cfg):
    rows = J.trades()
    for r in rows:
        if r["status"] == "open":
            try:
                px = P.last_bar(r["symbol"], now)["c"]
            except C.DeskError:
                px = float(r.get("cur_stop") or r["stop"])          # no price: assume the stop
            P.close_row(r, px, now.strftime("%H:%M"), reason, cfg)
            C.post("exit", now, symbol=r["symbol"], price=r["exit_price"], reason=reason, net=r["net_pnl"],
                   charges=r["charges"])
    J.save(rows)


def open_loss(now):
    loss = 0.0
    for r in J.trades():
        if r["status"] == "open":
            try:
                px = P.last_bar(r["symbol"], now)["c"]
            except C.DeskError:
                continue
            loss += min(0.0, (px - float(r["entry"])) * int(r["qty"]) * (1 if r["side"] == "long" else -1))
    return -loss


def guard(cfg, now):
    """Check every trigger; returns the active halt (or None)."""
    if halted(now):
        return halted(now)
    if os.path.exists(C.state_path("STOP")):
        halt(now, "STOP file found", "review", cfg=cfg)
    elif cfg["peak_equity"] and (cfg["peak_equity"] - cfg["equity"] + open_loss(now)) / cfg["peak_equity"] * 100 \
            >= cfg["kill_drawdown_pct"]:
        halt(now, "kill switch: equity %s%%+ below its peak" % cfg["kill_drawdown_pct"], "review", cfg=cfg)
    else:
        today = now.date().isoformat()
        lost = -sum(min(0.0, float(r["net_pnl"] or 0)) for r in J.trades()
                    if r["status"] == "closed" and r["date"] == today)
        if lost + open_loss(now) >= cfg["max_loss_per_day"]:
            halt(now, "day loss limit %s reached" % C.inr(cfg["max_loss_per_day"]), "day", cfg=cfg)
        elif errors(now) >= 3:
            halt(now, "3 unexpected errors today", "review", cfg=cfg)
        elif vix_jump(now) >= cfg["vix_jump_pct"]:
            halt(now, "India VIX up %.0f%% today" % vix_jump(now), "day", close=False)
        elif R.loss_streak(J.trades()) >= cfg["max_loss_streak"]:
            halt(now, "%d losses in a row" % cfg["max_loss_streak"], "review", close=False)
    return halted(now)


def vix_jump(now):
    try:
        d = C.fetch_bars("^INDIAVIX", "5d", "1d")
        prev = [b for b in d if b["t"].date() < now.date()]
        today = C.closed_bars(C.by_day(C.fetch_bars("^INDIAVIX", "1d", "5m")).get(now.date(), []), now)
        return (today[-1]["c"] - prev[-1]["c"]) / prev[-1]["c"] * 100 if prev and today else 0.0
    except C.DeskError:
        return 0.0


def errors(now, add=False):
    e = C.read_json("state/errors.json", {}) or {}
    n = e.get(now.date().isoformat(), 0) + (1 if add else 0)
    if add:
        C.write_json("state/errors.json", {now.date().isoformat(): n})
    return n


def resume(now):
    for f in ("state/halt.json", "STOP"):
        if os.path.exists(C.state_path(f)):
            os.remove(C.state_path(f))
    C.write_json("state/pause.json", {"cleared_at": now.date().isoformat() + now.strftime("%H:%M")})
    C.post("halt", now, reason="resumed by you", until="none")


# ---------- approver ----------

def decide(idea, cfg, now):
    """Yes or no for one idea. Returns the opened journal row, or None."""
    sym = idea["symbol"]

    def no(reason):
        C.post("rejected", now, symbol=sym, reason=reason)
        return None
    if not cfg["paper"]:
        return no("live mode: the approver only works in paper mode")
    if halted(now):
        return no("trading halted: " + halted(now)["reason"])
    stop = round((idea["or_low"] + idea["or_high"]) / 2, 2)
    plan = PL.build(sym, idea["entry"], stop, cfg, now, "reentry" if idea.get("reentry") else "orb")
    if not plan["qty"]:
        return no(plan["why"])
    reasons = R.check(plan, cfg, now)
    if reasons:
        return no("; ".join(reasons))
    plan["risk"] = {"verdict": "PASS", "reasons": [], "checked_at": now.isoformat(), "fingerprint": C.fingerprint(plan)}
    C.write_json("plan.json", plan)
    try:
        row = P.open_trade(plan, "yes", cfg, now, who="approver")
    except C.DeskError as e:
        return no(str(e))
    except Exception as e:                       # unexpected: count it, the guardian halts after 3
        errors(now, add=True)
        return no("error: %s" % str(e)[:80])
    C.post("approved", now, symbol=sym, qty=row["qty"], why=idea.get("why", ""), score=idea.get("score"))
    return row
