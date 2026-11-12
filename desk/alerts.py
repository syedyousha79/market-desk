"""The loop: runs the agents in order and relays their messages to Telegram.

    python desk/alerts.py setup     # link your Telegram chat (after sending 'hello' to the bot)
    python desk/alerts.py test      # send a test message
    python desk/alerts.py check     # one pass now
    python desk/alerts.py watch     # loop: every minute in market hours, every 15 minutes otherwise

Each pass: news scout -> signal agent -> position monitor -> flat-by warning,
then the messenger sends any new message-board posts. The bot only messages the
chat id in .env and never reads commands. Respects telegram_paused.
"""
import argparse
import sys
import time

import common as C
import news as N
import paper as P
import signals as SG


def setup():
    chats = {}
    for u in C.telegram_api("getUpdates").get("result", []):
        c = (u.get("message") or {}).get("chat") or {}
        if c.get("type") == "private":
            chats[c["id"]] = c.get("first_name") or ""
    if len(chats) != 1:
        print("Found %d private chats. Send 'hello' to your bot, then run setup again." % len(chats))
        return 1
    cid = next(iter(chats))
    C.set_env_value("TELEGRAM_CHAT_ID", cid)
    print("Saved your chat id to .env.")
    return 0


def text(e):
    k = e["kind"]
    if k == "news":
        return "NEWS %s: %s (%s)" % (",".join(e["stocks"]) or "MARKET", e["text"][:180], e["source"])
    if k == "signal":
        return "IDEA %s ~%s: %s. Ask Claude to plan it." % (e["symbol"], e["entry"], e["why"])
    if k == "entry":
        return "PAPER BUY %s x%s @ %s, stop %s, max loss %s" % (e["symbol"], e["qty"], e["price"], e["stop"], C.inr(e["max_loss"]))
    if k == "trail":
        return "STOP RAISED %s to %s (best %s): profit protected" % (e["symbol"], e["stop"], e["peak"])
    if k == "exit":
        return "PAPER EXIT %s @ %s (%s), net %s" % (e["symbol"], e["price"], e["reason"], C.inr(float(e["net"])))
    if k == "warn":
        return e["text"]
    return None


def check(cfg, now, quiet=False):
    st = C.read_json("state/loop.json", {}) or {}
    day = now.date().isoformat()
    if st.get("day") != day:
        st = {"day": day, "sent": 0, "flat_warned": False}
    last = st.get("news_at")
    if not last or now - C.dt.datetime.fromisoformat(last) >= C.dt.timedelta(minutes=2 if has_open() else 15):
        st["news_at"] = now.isoformat()
        try:
            for i in N.new_items(cfg, now):
                if i["stocks"] or cfg.get("news_alert_macro"):
                    C.post("news", now, stocks=i["stocks"], text=N.text_of(i), source=i["source"],
                           time=i["time"].isoformat(), bearish=i["tone_hint"] == "down?" or "governance" in i["tags"],
                           bullish=i["tone_hint"] == "up?")
        except Exception as e:                       # news must never stop the monitor
            if not quiet:
                print("news skipped: %s" % str(e)[:80])
    if C.phase(now, cfg) == "open":
        SG.ideas(cfg, now)
        P.update(cfg, now)
        flat = C.dt.datetime.combine(now.date(), C.hhmm(cfg["flat_by"]), C.IST)
        if not st["flat_warned"] and flat - C.dt.timedelta(minutes=10) <= now < flat:
            st["flat_warned"] = True
            C.post("warn", now, text="Flat-by %s in under 10 minutes: open trades will be closed." % cfg["flat_by"])
    evs = C.events(now.date())
    new, st["sent"] = evs[st["sent"]:], len(evs)
    C.write_json("state/loop.json", st)
    for e in new:
        msg = text(e)
        if msg:
            C.telegram_send(msg)
            if not quiet:
                print(msg)
    return new


def has_open():
    import journal as J
    return any(r["status"] == "open" for r in J.trades())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for n in ("setup", "test", "watch"):
        sub.add_parser(n)
    sub.add_parser("check").add_argument("--at")
    a = p.parse_args(argv)
    cfg = C.load_config()
    if a.cmd == "setup":
        return setup()
    if cfg.get("telegram_paused") and a.cmd == "test":
        print("Telegram is paused (telegram_paused in trader/config.json).")
        return 0
    if a.cmd == "test":
        ok = C.telegram_send("Market Desk is connected.")
        print("Sent." if ok else "Not sent: run setup first, or check the token in .env.")
        return 0 if ok else 1
    if a.cmd == "check":
        check(cfg, C.now_ist(a.at))
        return 0
    print("Loop running. Ctrl+C to stop.%s" % (" Telegram is paused; messages print here only." if cfg.get("telegram_paused") else ""))
    while True:
        now = C.now_ist()
        try:
            check(cfg, now)
        except C.DeskError as e:
            print("pass failed: %s" % e)
        time.sleep(60 if C.phase(now, cfg) == "open" else 900)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Stopped.")
