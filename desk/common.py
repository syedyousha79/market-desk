"""Shared pieces for the NSE desk: IST clock, NSE session, Yahoo data,
Zerodha intraday charges, config, state files and Telegram.

Standard library only. Design borrowed from day-trader-skill (MIT).
Research and tooling, not investment advice.
"""
import datetime as dt
import json
import math
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

for _s in (sys.stdout, sys.stderr):   # Windows consoles default to cp1252, which has no ₹
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADER = os.path.join(ROOT, "trader")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")   # no daylight saving in India
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) market-desk/0.1"

OPEN = dt.time(9, 15)
CLOSE = dt.time(15, 30)
BAR_MIN = 5

DEFAULTS = {
    "paper": True,
    "account_inr": 1000,
    "mis_leverage": 5,              # Zerodha intraday (MIS) leverage on many stocks; check per stock
    "max_loss_per_trade": 25,       # rupees, charges included
    "max_loss_per_day": 25,
    "max_loss_per_week": 100,
    "max_trades_per_day": 1,
    "min_reward_risk": 1.0,         # after charges, measured at the planning target
    "target_r": 1.5,                # planning target only; exits trail instead of capping profit
    "max_open": 1,
    "max_loss_streak": 3,           # pause after this many losses in a row (risk.py --resume)
    "breakeven_r": 1.0,             # at +1R the stop moves to entry
    "trail_r": 1.0,                 # then trails 1R behind the best price
    "time_stop_min": 30, "time_stop_r": 0.5,   # exit if not +0.5R after 30 minutes
    "stale_data_min": 10,           # exit an open trade if prices stop arriving
    "in_play_top": 3,               # only the 3 breakouts with the highest relative volume
    "chase_pct": 1.0,               # skip if price ran >1% past the trigger close
    "news_veto_hours": 3,
    "no_entries_before": "09:30",
    "no_entries_after": "14:30",
    "flat_by": "15:15",             # ahead of Zerodha's ~15:20 MIS auto square-off
    "or_minutes": 15,               # opening range 09:15-09:30
    "volume_multiple": 1.5,
    "allow_short": False,
    "telegram_paused": False,       # True blocks every Telegram message
    "news_alert_macro": False,      # True also sends market-wide headlines (busy)
    "holidays": [],                 # "YYYY-MM-DD" NSE trading holidays; fill from nseindia.com
    "watchlist": [
        "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK",
        "KOTAKBANK", "LT", "ITC", "BHARTIARTL", "TMPV", "MARUTI", "SUNPHARMA",
        "BAJFINANCE", "HINDUNILVR", "M&M", "TATASTEEL", "NTPC", "LICI",
    ],
    # Model per job when agents run: cheap models for simple, frequent work.
    "models": {"news_rating": "haiku", "trade_explainer": "sonnet", "weekly_review": "sonnet", "hard_research": "opus"},
    "context": ["^NSEI", "^NSEBANK", "^INDIAVIX"],
    # Zerodha equity intraday charges as fractions. Check zerodha.com/charges and update.
    "charges": {
        "brokerage_rate": 0.0003, "brokerage_cap": 20.0,   # per executed order
        "stt_sell": 0.00025,
        "exchange_txn": 0.0000307,                         # NSE, per Zerodha charges page (Nov 2026)
        "sebi": 0.000001,
        "stamp_buy": 0.00003,
        "gst": 0.18,
    },
}


class DeskError(Exception):
    pass


# ---------- clock ----------

def now_ist(override=None):
    """Current IST time, or a pinned one: 'YYYY-MM-DD HH:MM'."""
    if override:
        return dt.datetime.strptime(override, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    return dt.datetime.now(IST).replace(microsecond=0)


def hhmm(text):
    try:
        h, m = text.strip().split(":")
        return dt.time(int(h), int(m))
    except Exception:
        raise DeskError("expected a time like 09:30, got %r" % text)


def is_trading_day(day, cfg):
    return day.weekday() < 5 and day.isoformat() not in set(cfg.get("holidays") or [])


def phase(now, cfg):
    if not is_trading_day(now.date(), cfg):
        return "closed (weekend or holiday)"
    t = now.time()
    if t < dt.time(9, 0):
        return "before pre-open"
    if t < OPEN:
        return "pre-open"
    if t < CLOSE:
        return "open"
    return "closed"


# ---------- config, env, state ----------

def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    path = os.path.join(TRADER, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def load_env():
    env = {}
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def set_env_value(key, value):
    path = os.path.join(ROOT, ".env")
    lines = open(path, encoding="utf-8-sig").read().splitlines() if os.path.exists(path) else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append("%s=%s" % (key, value))
            done = True
        else:
            out.append(line)
    if not done:
        out.append("%s=%s" % (key, value))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def state_path(name):
    return os.path.join(TRADER, name)


def read_json(name, default=None):
    path = state_path(name)
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        raise DeskError("trader/%s is damaged (%s); delete it or restore it" % (name, str(e)[:60]))


def write_json(name, data):
    path = state_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


# ---------- symbols and data ----------

def yahoo_symbol(t):
    t = t.strip().upper()
    if t.startswith("^") or "=" in t or t.endswith(".NS") or t.endswith(".BO"):   # ^index, INR=X currency
        return t
    return t + ".NS"


def plain(sym):
    return sym[:-3] if sym.endswith((".NS", ".BO")) else sym


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_bars(symbol, rng="5d", interval="5m"):
    """Bars as dicts {t (IST datetime, bar start), o, h, l, c, v}, oldest first.

    NSE_OFFLINE=<dir> reads <dir>/<SYMBOL>-<interval>.json instead (Yahoo's chart JSON).
    """
    sym = yahoo_symbol(symbol)
    off = os.environ.get("NSE_OFFLINE")
    if off:
        path = os.path.join(off, "%s-%s.json" % (sym, interval))
        if not os.path.exists(path):
            raise DeskError("no offline data for %s" % sym)
        with open(path, "rb") as f:
            raw = f.read()
    else:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (
            urllib.parse.quote(sym), rng, interval)
        try:
            raw = _get(url)
        except Exception as e:
            raise DeskError("Yahoo did not return %s: %s" % (sym, e))
    res = (json.loads(raw).get("chart") or {}).get("result")
    if not res:
        raise DeskError("Yahoo has no data for %s (check the symbol)" % sym)
    r = res[0]
    q = r["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        bars.append({"t": dt.datetime.fromtimestamp(ts, IST), "o": o, "h": h, "l": l, "c": c, "v": v or 0})
    return bars


def by_day(bars):
    days = {}
    for b in bars:
        days.setdefault(b["t"].date(), []).append(b)
    return days


def closed_bars(bars, now):
    """Only bars that have finished by `now` (a 5-minute bar starting 10:10 closes 10:15)."""
    return [b for b in bars if b["t"] + dt.timedelta(minutes=BAR_MIN) <= now]


# ---------- money ----------

def charges(buy_value, sell_value, cfg):
    """Zerodha equity intraday round-trip charges in rupees."""
    c = cfg["charges"]
    brk = sum(min(v * c["brokerage_rate"], c["brokerage_cap"]) for v in (buy_value, sell_value) if v > 0)
    stt = sell_value * c["stt_sell"]
    exch = (buy_value + sell_value) * c["exchange_txn"]
    sebi = (buy_value + sell_value) * c["sebi"]
    stamp = buy_value * c["stamp_buy"]
    gst = (brk + exch + sebi) * c["gst"]
    total = brk + stt + exch + sebi + stamp + gst
    return {"brokerage": round(brk, 2), "stt": round(stt, 2), "exchange": round(exch, 2),
            "sebi": round(sebi, 2), "stamp": round(stamp, 2), "gst": round(gst, 2), "total": round(total, 2)}


# ---------- message board: how the agents talk ----------
# Each agent posts short events to trader/bus/<date>.jsonl; the next agent reads them.

def post(kind, at, **data):
    ev = {"at": at.isoformat(), "kind": kind, **data}
    path = state_path(os.path.join("bus", at.date().isoformat() + ".jsonl"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, default=str) + "\n")
    return ev


def events(day, kind=None):
    path = state_path(os.path.join("bus", day.isoformat() + ".jsonl"))
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        evs = [json.loads(line) for line in f if line.strip()]
    return [e for e in evs if kind is None or e["kind"] == kind]


def bad_news(symbol, day, since=None):
    """Times of bearish news on a symbol today (from the news scout's posts)."""
    out = []
    for e in events(day, "news"):
        t = dt.datetime.fromisoformat(e["time"])
        if symbol in e.get("stocks", []) and e.get("bearish") and (since is None or t >= since):
            out.append(t)
    return sorted(out)


def fingerprint(order):
    """Short hash of the fields that define an order; any change needs a new risk check."""
    import hashlib
    keys = ("symbol", "side", "qty", "entry", "stop", "target", "date")
    return hashlib.sha256(json.dumps({k: order.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()[:12]


def inr(x):
    sign = "-" if x < 0 else ""
    return "%s₹%s" % (sign, "{:,.2f}".format(abs(x)))


def floor_qty(x):
    return max(0, int(math.floor(x + 1e-9)))


# ---------- Telegram ----------

def _curl():
    return shutil.which("curl")


def telegram_api(method, params=None):
    if os.environ.get("NSE_OFFLINE") or os.environ.get("NSE_NO_TELEGRAM"):
        raise DeskError("Telegram is disabled in offline and test runs")
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token or "paste" in token:
        raise DeskError("no Telegram token in .env")
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except (ssl.SSLError, urllib.error.URLError) as e:
        # Some antivirus tools break Python's certificate check on this PC; curl uses the Windows store.
        if not _curl():
            raise DeskError("Telegram unreachable: %s" % e)
        args = [_curl(), "-s", "--max-time", "20", url]
        for k, v in (params or {}).items():
            args += ["--data-urlencode", "%s=%s" % (k, v)]
        out = subprocess.run(args, capture_output=True, timeout=30)
        if out.returncode != 0:
            raise DeskError("Telegram unreachable (curl exit %d)" % out.returncode)
        return json.loads(out.stdout or b"{}")


def telegram_send(text):
    """Send to the one chat in .env. Returns True on success. Never raises."""
    if os.environ.get("NSE_NO_TELEGRAM") or load_config().get("telegram_paused"):
        return False
    env = load_env()
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not chat or not chat.lstrip("-").isdigit():
        return False
    try:
        return bool(telegram_api("sendMessage", {"chat_id": chat, "text": text}).get("ok"))
    except DeskError:
        return False
