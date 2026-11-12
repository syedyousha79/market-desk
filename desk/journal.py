"""The trade journal: trader/journal.csv, one row per paper (or live) trade.

    python desk/journal.py            # summary: trades, win rate, net P&L after charges
"""
import csv
import os

import common as C

FIELDS = ["id", "date", "symbol", "side", "qty", "entry", "stop", "target", "max_loss", "opened_at",
          "status", "exit_price", "exit_at", "exit_reason", "gross_pnl", "charges", "net_pnl",
          "setup", "paper", "reply"]


def path():
    return C.state_path("journal.csv")


def trades():
    """All rows. A damaged journal raises DeskError, so the risk check blocks rather than guessing."""
    if not os.path.exists(path()):
        return []
    try:
        with open(path(), newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            header = reader.fieldnames or []
    except (csv.Error, UnicodeDecodeError, ValueError) as e:
        raise C.DeskError("trader/journal.csv is damaged (%s)" % str(e)[:60])
    missing = [k for k in ("date", "symbol", "status", "net_pnl", "max_loss") if k not in header]
    if missing:
        raise C.DeskError("trader/journal.csv is damaged: missing columns %s" % ", ".join(missing))
    for r in rows:
        for k in ("net_pnl", "max_loss", "charges"):
            try:
                float(r.get(k) or 0)
            except ValueError:
                raise C.DeskError("trader/journal.csv has a bad number in %s: %r" % (k, r.get(k)))
    return rows


def save(rows):
    os.makedirs(C.TRADER, exist_ok=True)
    tmp = path() + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, path())


def summary(rows=None):
    rows = trades() if rows is None else rows
    closed = [r for r in rows if r["status"] == "closed"]
    wins = [r for r in closed if float(r["net_pnl"]) > 0]
    net = sum(float(r["net_pnl"]) for r in closed)
    ch = sum(float(r["charges"] or 0) for r in closed)
    return {"trades": len(closed), "open": sum(1 for r in rows if r["status"] == "open"),
            "wins": len(wins), "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "net_pnl": round(net, 2), "charges": round(ch, 2)}


if __name__ == "__main__":
    s = summary()
    print("Closed trades %d, open %d, wins %d (%s%%), net P&L %s after %s charges"
          % (s["trades"], s["open"], s["wins"], s["win_rate"] if s["win_rate"] is not None else "-",
             C.inr(s["net_pnl"]), C.inr(s["charges"])))
    for r in trades()[-10:]:
        print("  %s %-10s %s %s @ %s -> %s %s  net %s" % (r["date"], r["symbol"], r["side"], r["qty"], r["entry"],
              r["exit_price"] or "-", r["exit_reason"] or r["status"], r["net_pnl"] or "-"))
