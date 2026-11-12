"""Telegram alerts and the market watcher.

    python desk/alerts.py setup        # finds your chat with the bot and saves its id in .env
    python desk/alerts.py test         # sends a test message
    python desk/alerts.py check        # one pass: new breakouts, paper exits, flat-by warning
    python desk/alerts.py watch        # repeat check every 5 minutes while the market is open

The bot only ever messages the one chat id in .env and never reads commands,
so strangers who find the bot get nothing. The token stays in .env.
"""
import argparse
import sys
import time

import common as C
import news as N
import paper as P
import scan as S


def setup():
    res = C.telegram_api("getUpdates")
    chats = {}
    for u in res.get("result", []):
        m = u.get("message") or {}
        c = m.get("chat") or {}
        if c.get("type") == "private":
            chats[c["id"]] = c.get("first_name") or ""
    if not chats:
        print("No private chat found. Open your bot in Telegram, press Start, send 'hello', then run setup again.")
        return 1
    if len(chats) > 1:
        print("%d different people have messaged the bot. Run setup right after you send 'hello', or tell Claude." % len(chats))
        return 1
    cid = next(iter(chats))
    C.set_env_value("TELEGRAM_CHAT_ID", cid)
    print("Saved your chat id to .env (name on the chat: %s)." % chats[cid])
    return 0


def check(cfg, now, quiet=False):
    sent = []
    st = C.read_json("state/alerts.json", {}) or {}
    day = now.date().isoformat()
    if st.get("day") != day:
        st = {"day": day, "breakouts": [], "flat_warned": False}
    if C.phase(now, cfg) == "open":
        # new breakouts
        hits = []
        for s in cfg["watchlist"]:
            try:
                r = S.orb_for(s, now, cfg)
            except C.DeskError:
                continue
            if r.get("status") == "breakout" and r["symbol"] not in st["breakouts"]:
                hits.append(r)
        for r in hits:
            st["breakouts"].append(r["symbol"])
            msg = ("ORB %s %s: %s close %s above range %s-%s, volume %sx. Ask Claude to plan it."
                   % (r["symbol"], r["side"], r["trigger_bar"], r["trigger_close"], r["or_low"], r["or_high"], r["volume_x"]))
            sent.append(msg)
        # paper exits
        for r in P.update(cfg, now):
            sent.append("PAPER EXIT %s @ %s (%s), net %s" % (r["symbol"], r["exit_price"], r["exit_reason"], C.inr(float(r["net_pnl"]))))
        # flat-by warning, 10 minutes ahead
        flat = C.dt.datetime.combine(now.date(), C.hhmm(cfg["flat_by"]), C.IST)
        if not st["flat_warned"] and flat - C.dt.timedelta(minutes=10) <= now < flat:
            st["flat_warned"] = True
            sent.append("Flat-by %s in under 10 minutes: close any intraday positions." % cfg["flat_by"])
    # news, around the clock, at most every 15 minutes
    last = st.get("news_at")
    if not last or now - C.dt.datetime.fromisoformat(last) >= C.dt.timedelta(minutes=15):
        st["news_at"] = now.isoformat()
        try:
            for i in N.new_items(cfg, now):
                if i["stocks"] or cfg.get("news_alert_macro"):
                    who = ",".join(i["stocks"]) or "MARKET"
                    text = N.text_of(i)
                    sent.append("NEWS %s: %s (%s)" % (who, text[:200], i["source"]))
        except Exception as e:      # news must never break price alerts
            if not quiet:
                print("news skipped: %s" % str(e)[:80])
    C.write_json("state/alerts.json", st)
    for m in sent:
        C.telegram_send(m)
        if not quiet:
            print(m)
    if not sent and not quiet:
        print("Nothing new (%s, market %s)." % (now.strftime("%H:%M"), C.phase(now, cfg)))
    return sent


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("test")
    sub.add_parser("check").add_argument("--at")
    w = sub.add_parser("watch")
    w.add_argument("--every", type=int, default=5, help="minutes between checks")
    a = p.parse_args(argv)
    cfg = C.load_config()
    if a.cmd == "setup":
        return setup()
    if a.cmd == "test":
        ok = C.telegram_send("Market Desk is connected. Alerts will arrive here.")
        print("Sent." if ok else "Not sent: run setup first, or check the token in .env.")
        return 0 if ok else 1
    if a.cmd == "check":
        check(cfg, C.now_ist(a.at))
        return 0
    if a.cmd == "watch":
        print("Watching every %d minutes. Ctrl+C to stop." % a.every)
        while True:
            now = C.now_ist()
            try:
                check(cfg, now)
            except C.DeskError as e:
                print("check failed: %s" % e)
            time.sleep(a.every * 60)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Stopped.")
