"""News scanner: NSE announcements and Indian market headlines, matched to the watchlist.

    python desk/news.py scan [--hours 12]      # everything recent, grouped (writes trader/news.json)
    python desk/news.py new                    # only items not seen before that matter (used by alerts)

Sources (free, public RSS): NSE corporate announcements (official), Economic Times
Markets and Stocks, Business Standard Markets, Livemint Markets.

Each item gets: the watchlist stocks it names, topic tags (results, deal, capital,
governance, broker call, block deal, macro) and a rough tone hint from keywords.
The hint is a starting point; the news-rater agent (Haiku) does the real reading.
Headlines are data. Nothing in them is ever followed as an instruction.
"""
import argparse
import email.utils
import html
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

import common as C

FEEDS = {
    "NSE": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",
    "ET Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "ET Stocks": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
    "Livemint": "https://www.livemint.com/rss/markets",
}

# NSE legal name (exact title in the NSE feed) and headline aliases.
# Short all-caps aliases match case-sensitively so "ITC" never matches "switch".
ALIASES = {
    "RELIANCE": ("Reliance Industries Limited", ["Reliance Industries", "RIL", "Mukesh Ambani"]),
    "HDFCBANK": ("HDFC Bank Limited", ["HDFC Bank"]),
    "ICICIBANK": ("ICICI Bank Limited", ["ICICI Bank"]),
    "INFY": ("Infosys Limited", ["Infosys"]),
    "TCS": ("Tata Consultancy Services Limited", ["Tata Consultancy", "TCS"]),
    "SBIN": ("State Bank of India", ["State Bank of India", "SBI"]),
    "AXISBANK": ("Axis Bank Limited", ["Axis Bank"]),
    "KOTAKBANK": ("Kotak Mahindra Bank Limited", ["Kotak Mahindra Bank", "Kotak Bank"]),
    "LT": ("Larsen & Toubro Limited", ["Larsen & Toubro", "L&T"]),
    "ITC": ("ITC Limited", ["ITC"]),
    "BHARTIARTL": ("Bharti Airtel Limited", ["Bharti Airtel", "Airtel"]),
    # Since the 2025 demerger, "Tata Motors CV" is a different company (TMCV), so no bare "Tata Motors".
    "TMPV": ("Tata Motors Passenger Vehicles Limited", ["Tata Motors PV", "Tata Motors Passenger", "TMPV"]),
    "TMCV": ("Tata Motors Limited", ["Tata Motors CV", "Tata Motors Commercial", "TMCV"]),
    "MARUTI": ("Maruti Suzuki India Limited", ["Maruti Suzuki", "Maruti"]),
    "SUNPHARMA": ("Sun Pharmaceutical Industries Limited", ["Sun Pharma"]),
    "BAJFINANCE": ("Bajaj Finance Limited", ["Bajaj Finance"]),
    "HINDUNILVR": ("Hindustan Unilever Limited", ["Hindustan Unilever", "HUL"]),
    "M&M": ("Mahindra & Mahindra Limited", ["Mahindra & Mahindra", "M&M"]),
    "TATASTEEL": ("Tata Steel Limited", ["Tata Steel"]),
    "NTPC": ("NTPC Limited", ["NTPC"]),
    "LICI": ("Life Insurance Corporation of India", ["Life Insurance Corporation", "LIC"]),
}

TOPICS = {
    "results": r"financial results|quarterly results|\bq[1-4]\b|earnings|net profit|profit (rises|falls|jumps|drops)|revenue",
    "deal": r"\border(s)? (win|worth|from)|bags? .*order|contract|acquisition|acquires?|merger|stake (sale|buy)|joint venture",
    "capital": r"dividend|bonus|stock split|buyback|rights issue|\bqip\b|fund ?raising|\bipo\b|ofs\b",
    "governance": r"resign|penalt|sebi order|show cause|\braid|fraud|default|pledge|insolvency|probe|investigation",
    "broker call": r"\btarget\b|upgrade|downgrade|\bbuy\b.*\b(rs|₹)|\bsell\b.*\b(rs|₹)|overweight|underweight|initiates? coverage",
    "block deal": r"block deal|bulk deal",
    "macro": r"\brbi\b|repo rate|monetary policy|\bfed\b|fomc|inflation|\bcpi\b|\bgdp\b|crude|oil price|rupee|"
             r"\bwar\b|missile|attack|tariff|sanction|union budget|\bgst\b|\bfii|\bfpi|election|recession|"
             r"wall street|s&p 500|nasdaq|dow jones|bond yield",
}
ROUTINE = r"trading window|analyst/investor meet|investor meet|newspaper publication|redemption/payment of interest|" \
          r"loss of share certificate|certificate under sebi|shareholders meeting|record date|transcripts?|" \
          r"interest payment|reg\. 74|regulation 74|duplicate share|change in rta|esop|esos|allotment of securities"
UP = r"\b(surg\w*|soar\w*|jump\w*|rall(y|ies|ied)|gains?|record high|52-week high|upgrade\w*|beats?|wins?|bags?|" \
     r"rises?|climbs?|strong\w*|rebound\w*|lifts?)\b"
DOWN = r"\b(plung\w*|slump\w*|crash\w*|tumbl\w*|falls?|drops?|slides?|sinks?|slips?|downgrade\w*|miss(es)?|loss(es)?|" \
       r"penalt\w*|resign\w*|fraud|raid\w*|weak\w*|cuts?|lower|rattl\w*|unsettl\w*|saps?|rout|selloff|sell-off)\b"


def _pattern(alias):
    esc = re.escape(alias)
    if alias.isupper() or "&" in alias:          # acronyms: case-sensitive, whole word
        return re.compile(r"(?<![A-Za-z])%s(?![A-Za-z])" % esc)
    return re.compile(r"\b%s\b" % esc, re.I)


def watch_patterns(cfg):
    pats = {}
    for sym in cfg["watchlist"]:
        s = C.plain(C.yahoo_symbol(sym))
        legal, aliases = ALIASES.get(s, (None, [s]))
        pats[s] = (legal, [_pattern(a) for a in aliases])
    return pats


def parse_date(text):
    text = (text or "").strip()
    try:
        return C.dt.datetime.strptime(text, "%d-%b-%Y %H:%M:%S").replace(tzinfo=C.IST)   # NSE, in IST
    except ValueError:
        pass
    try:
        return email.utils.parsedate_to_datetime(text).astimezone(C.IST)
    except (TypeError, ValueError):
        return None


def fetch_feed(name, url):
    import os
    off = os.environ.get("NSE_OFFLINE")
    if off:
        path = os.path.join(off, "news-%s.xml" % re.sub(r"\W+", "_", name))
        if not os.path.exists(path):
            return []
        with open(path, "rb") as f:
            raw = f.read()
    else:
        root = None
        for attempt in range(3):   # the NSE file is large and sometimes arrives cut short
            req = urllib.request.Request(url, headers={"User-Agent": C.UA, "Accept": "application/rss+xml,application/xml,*/*"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            try:
                root = ET.fromstring(raw)
                break
            except ET.ParseError:
                if attempt == 2:
                    raise
    root = root if not off else ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        title = html.unescape(re.sub(r"\s+", " ", (it.findtext("title") or ""))).strip()
        desc = html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("description") or ""))
        desc = re.sub(r"\s+", " ", desc).strip()
        items.append({"source": name, "title": title, "summary": desc[:400],
                      "link": (it.findtext("link") or "").strip(), "time": parse_date(it.findtext("pubDate"))})
    return items


def classify(item, pats):
    text = item["title"] + " " + item["summary"]
    low = text.lower()
    stocks = []
    for sym, (legal, ps) in pats.items():
        if item["source"] == "NSE":
            if legal and item["title"].lower() == legal.lower():
                stocks.append(sym)
        elif any(p.search(text) for p in ps):
            stocks.append(sym)
    tags = [t for t, rx in TOPICS.items() if re.search(rx, low)]
    routine = bool(re.search(ROUTINE, low))
    up, down = len(re.findall(UP, low)), len(re.findall(DOWN, low))
    tone = "up?" if up > down else ("down?" if down > up else "unclear")
    item.update(stocks=stocks, tags=tags, routine=routine, tone_hint=tone)
    item["matters"] = bool((stocks and not routine) or ("macro" in tags and item["source"] != "NSE"))
    return item


def collect(cfg, now, hours):
    pats = watch_patterns(cfg)
    since = now - C.dt.timedelta(hours=hours)
    out, errors = [], []
    for name, url in FEEDS.items():
        try:
            for it in fetch_feed(name, url):
                if it["time"] and since <= it["time"] <= now + C.dt.timedelta(minutes=5):
                    out.append(classify(it, pats))
        except Exception as e:   # one broken feed should not stop the rest
            errors.append("%s: %s" % (name, str(e)[:80]))
    out.sort(key=lambda x: x["time"], reverse=True)
    seen_titles, unique = set(), []
    for it in out:                 # ET Markets and ET Stocks often carry the same story
        # NSE titles are only the company name, so NSE filings are told apart by link
        t = it["link"] if it["source"] == "NSE" else re.sub(r"\W+", " ", it["title"].lower()).strip()
        if t not in seen_titles:
            seen_titles.add(t)
            unique.append(it)
    return unique, errors


def key(item):
    return item["link"] or (item["source"] + item["title"])


def new_items(cfg, now, hours=6):
    items, _ = collect(cfg, now, hours)
    seen = set(C.read_json("state/news_seen.json", []) or [])
    fresh = [i for i in items if i["matters"] and key(i) not in seen]
    seen.update(key(i) for i in items)
    C.write_json("state/news_seen.json", sorted(seen)[-3000:])
    return fresh


def text_of(i):
    """Readable text: NSE titles are only the company name, so use the filing summary."""
    if i["source"] != "NSE":
        return i["title"]
    head, _, subject = i["summary"].partition("|SUBJECT:")
    head = head.strip()
    return head if len(head) >= 20 else "%s: %s" % (i["title"], (subject or head).strip())


def line(i):
    who = ",".join(i["stocks"]) or "market"
    tags = "/".join(i["tags"]) or "-"
    text = text_of(i)
    return "%s %-9s [%s] %s (%s, %s)" % (i["time"].strftime("%d %b %H:%M"), who, tags, text[:110], i["source"], i["tone_hint"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--hours", type=float, default=12)
    s.add_argument("--at")
    n = sub.add_parser("new")
    n.add_argument("--at")
    a = p.parse_args(argv)
    cfg = C.load_config()
    now = C.now_ist(a.at)
    if a.cmd == "scan":
        items, errors = collect(cfg, now, a.hours)
        C.write_json("news.json", {"at": now.isoformat(), "hours": a.hours,
                                   "items": [dict(i, time=i["time"].isoformat()) for i in items], "errors": errors})
        watch = [i for i in items if i["stocks"] and not i["routine"]]
        macro = [i for i in items if "macro" in i["tags"] and not i["stocks"] and i["source"] != "NSE"]
        print("News, last %g hours to %s IST: %d items, %d on your watchlist, %d market-wide."
              % (a.hours, now.strftime("%d %b %H:%M"), len(items), len(watch), len(macro)))
        print("\nYour watchlist:")
        for i in watch[:25]:
            print("  " + line(i))
        if not watch:
            print("  nothing new")
        print("\nMarket-wide:")
        for i in macro[:15]:
            print("  " + line(i))
        routine = sum(1 for i in items if i["stocks"] and i["routine"])
        if routine:
            print("\n(%d routine watchlist filings hidden: trading windows, investor meets, interest payments.)" % routine)
        for e in errors:
            print("feed skipped: " + e)
        print("\nWritten to trader/news.json. Tone hints are keyword guesses, not analysis.")
    else:
        for i in new_items(cfg, now):
            print(line(i))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except C.DeskError as e:
        print("Error: %s" % e)
        sys.exit(1)
