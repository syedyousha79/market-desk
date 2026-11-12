"""Trade planner: stop, target, quantity and charges for one setup, before entry.

    python desk/plan.py RELIANCE                 # uses the breakout in trader/scan.json
    python desk/plan.py RELIANCE --entry 1341.5 --stop 1335 [--target 1352]
    python desk/plan.py RELIANCE --stop-rule or-low --target-r 2

Stop rules: or-mid (default, middle of the opening range) or or-low.
Target: --target, or --target-r times the per-share risk (default config target_r).
Quantity: the largest whole number of shares whose loss at the stop, charges
included, stays within max_loss_per_trade, and whose value fits in
account_inr x mis_leverage. Writes trader/plan.json. Never places anything.
"""
import argparse
import sys

import common as C


def size(entry, stop, target, cfg):
    side = 1 if stop < entry else -1
    per_share = abs(entry - stop)
    if per_share <= 0:
        raise C.DeskError("the stop must differ from the entry")
    cap_value = cfg["account_inr"] * cfg["mis_leverage"]
    qty = min(C.floor_qty(cfg["max_loss_per_trade"] / per_share), C.floor_qty(cap_value / entry))
    while qty > 0:
        buy, sell = (entry, stop) if side == 1 else (stop, entry)
        ch_stop = C.charges(qty * buy, qty * sell, cfg)["total"]
        if qty * per_share + ch_stop <= cfg["max_loss_per_trade"]:
            break
        qty -= 1
    if qty == 0:
        return {"qty": 0, "why": "even 1 share would lose more than %s at the stop once charges are added"
                % C.inr(cfg["max_loss_per_trade"])}
    buy_s, sell_s = (entry, stop) if side == 1 else (stop, entry)
    buy_t, sell_t = (entry, target) if side == 1 else (target, entry)
    ch_stop = C.charges(qty * buy_s, qty * sell_s, cfg)
    ch_tgt = C.charges(qty * buy_t, qty * sell_t, cfg)
    loss = qty * per_share + ch_stop["total"]
    gain = qty * abs(target - entry) - ch_tgt["total"]
    return {"qty": qty, "side": "long" if side == 1 else "short", "value": round(qty * entry, 2),
            "margin_needed": round(qty * entry / cfg["mis_leverage"], 2),
            "max_loss": round(loss, 2), "net_gain_at_target": round(gain, 2),
            "charges_at_stop": ch_stop, "charges_at_target": ch_tgt,
            "net_reward_risk": round(gain / loss, 2) if loss else None}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("symbol")
    p.add_argument("--entry", type=float)
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--stop-rule", choices=["or-mid", "or-low"], default="or-mid")
    p.add_argument("--target-r", type=float)
    p.add_argument("--at", help='pin the clock: "YYYY-MM-DD HH:MM" IST')
    a = p.parse_args(argv)
    cfg = C.load_config()
    now = C.now_ist(a.at)
    sym = C.plain(C.yahoo_symbol(a.symbol))
    hit = None
    scan = C.read_json("scan.json", {}) or {}
    for h in scan.get("hits", []):
        if h["symbol"] == sym:
            hit = h
    entry, stop = a.entry, a.stop
    if entry is None:
        if not hit:
            raise C.DeskError("%s has no breakout in trader/scan.json; run scan.py orb or pass --entry and --stop" % sym)
        entry = hit["trigger_close"]
    if stop is None:
        if not hit:
            raise C.DeskError("pass --stop, or scan first so the opening range is known")
        long_ = hit.get("side", "long") == "long"
        stop = hit["or_mid"] if a.stop_rule == "or-mid" else (hit["or_low"] if long_ else hit["or_high"])
    r = a.target_r or cfg.get("target_r", 1.5)
    target = a.target if a.target is not None else round(entry + r * (entry - stop), 2)
    s = size(entry, stop, target, cfg)
    order = {"symbol": sym, "date": now.date().isoformat(), "made_at": now.strftime("%H:%M"),
             "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
             "flat_by": cfg["flat_by"], "product": "MIS", "paper": cfg["paper"],
             "setup": "orb" if hit else "manual", **s}
    if s["qty"]:
        order["fingerprint"] = C.fingerprint(order)
    C.write_json("plan.json", order)

    print("PLAN %s (%s), %s account" % (sym, order["setup"], "PAPER" if cfg["paper"] else "LIVE"))
    if not s["qty"]:
        print("  No trade: " + s["why"])
        return
    print("  %s %d %s @ ~%s  (value %s, margin ~%s at %dx)"
          % ("BUY" if s["side"] == "long" else "SELL", s["qty"], sym, entry, C.inr(s["value"]),
             C.inr(s["margin_needed"]), cfg["mis_leverage"]))
    print("  stop-loss %s   target %s   out by %s either way" % (stop, target, cfg["flat_by"]))
    print("  max loss at stop %s (charges %s)" % (C.inr(s["max_loss"]), C.inr(s["charges_at_stop"]["total"])))
    print("  net gain at target %s (charges %s)" % (C.inr(s["net_gain_at_target"]), C.inr(s["charges_at_target"]["total"])))
    print("  net reward:risk %s" % s["net_reward_risk"])
    print("Written to trader/plan.json. Next: python desk/risk.py")


if __name__ == "__main__":
    try:
        main()
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
