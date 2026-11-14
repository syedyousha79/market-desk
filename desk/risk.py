"""Risk gate: PASS or BLOCK the plan in trader/plan.json against your limits.

    python desk/risk.py [--at "YYYY-MM-DD HH:MM"]

Checks: a quantity exists, entry window, trades today, the day's and the week's
remaining loss budget (realised losses from trader/journal.csv plus open
trades' max loss), net reward:risk, margin, and shorts only if allowed.
Writes the verdict into trader/plan.json. A PASS is a rules check, not advice.
"""
import argparse
import sys

import common as C
import journal as J


def loss_streak(trades):
    """Losses in a row since the last review (risk.py --resume or brain.py resume)."""
    cleared = (C.read_json("state/pause.json", {}) or {}).get("cleared_at", "")
    n = 0
    for r in reversed([r for r in trades if r["status"] == "closed" and r["date"] + r["exit_at"] > cleared]):
        if float(r["net_pnl"] or 0) >= 0:
            break
        n += 1
    return n


def check(plan, cfg, now):
    reasons = []
    if not plan or not plan.get("qty"):
        return ["no sized plan; run plan.py first"]
    if plan["date"] != now.date().isoformat():
        reasons.append("the plan is from %s, not today" % plan["date"])
    if not C.is_trading_day(now.date(), cfg):
        reasons.append("not a trading day")
    t = now.time()
    if t < C.hhmm(cfg["no_entries_before"]) or t > C.hhmm(cfg["no_entries_after"]):
        reasons.append("outside the entry window %s-%s" % (cfg["no_entries_before"], cfg["no_entries_after"]))
    if plan["side"] == "short" and not cfg["allow_short"]:
        reasons.append("shorts are off (allow_short false)")
    today = now.date()
    week_start = today - C.dt.timedelta(days=today.weekday())
    trades = J.trades()
    n_today = sum(1 for r in trades if r["date"] == today.isoformat())
    if n_today >= cfg["max_trades_per_day"]:
        reasons.append("already %d trade(s) today (limit %d)" % (n_today, cfg["max_trades_per_day"]))
    lost_today = -sum(min(0.0, float(r["net_pnl"] or 0)) for r in trades if r["date"] == today.isoformat() and r["status"] == "closed")
    lost_week = -sum(min(0.0, float(r["net_pnl"] or 0)) for r in trades
                     if r["date"] >= week_start.isoformat() and r["status"] == "closed")
    open_rows = [r for r in trades if r["status"] == "open"]
    if len(open_rows) >= cfg["max_open"]:
        reasons.append("%d trade(s) already open (limit %d)" % (len(open_rows), cfg["max_open"]))
    streak = loss_streak(trades)
    if streak >= cfg["max_loss_streak"]:
        reasons.append("%d losses in a row: paused until reviewed (python desk/risk.py --resume)" % streak)
    open_risk = sum(float(r["max_loss"] or 0) for r in open_rows)
    room_day = cfg["max_loss_per_day"] - lost_today - open_risk
    room_week = cfg["max_loss_per_week"] - lost_week - open_risk
    if plan["max_loss"] > cfg["max_loss_per_trade"] + 0.005:
        reasons.append("max loss %s is over the per-trade limit %s" % (C.inr(plan["max_loss"]), C.inr(cfg["max_loss_per_trade"])))
    if plan["max_loss"] > room_day + 0.005:
        reasons.append("only %s of today's loss budget left" % C.inr(max(0, room_day)))
    if plan["max_loss"] > room_week + 0.005:
        reasons.append("only %s of this week's loss budget left" % C.inr(max(0, room_week)))
    ch = (plan.get("charges_at_stop") or {}).get("total", 0)
    if plan.get("max_loss") and ch / plan["max_loss"] > cfg["max_charges_r"]:
        reasons.append("charges %s are %.0f%% of the risk (limit %.0f%%)"
                       % (C.inr(ch), ch / plan["max_loss"] * 100, cfg["max_charges_r"] * 100))
    if (plan.get("net_reward_risk") or 0) < cfg["min_reward_risk"]:
        reasons.append("net reward:risk %s is under %s after charges" % (plan.get("net_reward_risk"), cfg["min_reward_risk"]))
    eq = cfg.get("equity", cfg["account_inr"])
    if plan["margin_needed"] > eq + 0.005:
        reasons.append("needs ~%s margin, equity is %s" % (C.inr(plan["margin_needed"]), C.inr(eq)))
    if cfg.get("drawdown_pct", 0) >= cfg["kill_drawdown_pct"]:
        reasons.append("KILL SWITCH: equity is %s%% below its peak (limit %s%%); all trading stopped until a full review"
                       % (cfg["drawdown_pct"], cfg["kill_drawdown_pct"]))
    return reasons


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--at")
    p.add_argument("--resume", action="store_true", help="lift the losing-streak pause after a review")
    a = p.parse_args(argv)
    cfg = C.with_money(C.load_config())
    now = C.now_ist(a.at)
    if a.resume:
        C.write_json("state/pause.json", {"cleared_at": now.date().isoformat() + now.strftime("%H:%M")})
        print("Losing-streak pause lifted. Earlier losses no longer count toward the streak.")
        return 0
    plan = C.read_json("plan.json")
    reasons = check(plan, cfg, now)
    verdict = "BLOCK" if reasons else "PASS"
    if plan:
        plan["risk"] = {"verdict": verdict, "reasons": reasons, "checked_at": now.isoformat(),
                        "fingerprint": C.fingerprint(plan)}
        C.write_json("plan.json", plan)
        C.post("risk", now, symbol=plan.get("symbol"), verdict=verdict, reasons=reasons, qty=plan.get("qty"),
               max_loss=plan.get("max_loss"))
    print("%s %s" % (verdict, plan.get("symbol", "") if plan else ""))
    for r in reasons:
        print("  - " + r)
    if verdict == "PASS":
        print("Next: show the ticket (python desk/paper.py ticket) and ask for an explicit yes.")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
