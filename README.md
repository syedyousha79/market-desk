# Market Desk

NSE intraday research and paper trading for a 3-person team, run through Claude Code.
Research and tooling only, not investment advice. Paper trading is the default.

## The flow

scan → plan → risk check → your explicit "yes" → paper trade → journal

| Ask Claude | Skill | Script |
|---|---|---|
| "How's the market? Any breakouts?" | /nse-scan | `desk/scan.py` |
| "Plan SBIN" | /nse-plan | `desk/plan.py`, `desk/risk.py` |
| "Take it" / "How are my trades?" | /nse-paper | `desk/paper.py`, `desk/journal.py` |
| "What's the news?" | /nse-news + news-rater agent (Haiku) | `desk/news.py` |
| "Any ideas?" | signal agent | `desk/signals.py` |
| "Morning brief" | morning-brief agent (Sonnet) | writes `trader/brief.json` |
| "Start watching" | /nse-alerts (the loop) | `desk/brain.py` |

## Which model does what

Cheap models do the frequent, simple work; bigger ones only where judgement matters.

| Job | Model | Where |
|---|---|---|
| Scanning, sizing, risk checks, paper fills, alerts | none (plain Python) | `desk/` |
| Rating news impact | Haiku | `.claude/agents/news-rater.md` |
| Morning brief and avoid list | Sonnet | `.claude/agents/morning-brief.md` |
| Weekly journal review | Sonnet | planned |
| Deep one-off research | Opus | on request only |

## How the agents talk

They post short events to a message board (`trader/bus/<date>.jsonl`): news -> signal -> risk -> entry
-> trail/exit. The loop (`brain.py watch`) runs them in order and relays posts to Telegram.
Exits: break-even at +1R, then a stop trailing 1R behind the best price, bad-news exit, 30-minute
time stop, stale-data exit, 15:15 flat-by. Entries always need your yes; exits never do.
One open trade at a time; 3 losses in a row pauses trading until `python desk/risk.py --resume`.

## Your limits (defaults, change in `trader/config.json`)

Account ₹1,000 · max loss ₹25 a trade, ₹25 a day, ₹100 a week · 1 trade a day ·
entries 09:30–14:30 · out by 15:15 · long only · net reward:risk ≥ 1 after Zerodha charges.
Copy `desk/config.example.json` to `trader/config.json` to change anything.

## Setup

1. Python 3.8+ (no packages needed).
2. Telegram: put your bot token in `.env`, send "hello" to the bot, run `python desk/brain.py setup`.
3. Tests: `python -m unittest discover -s tests -v` (48 tests, including stress tests)
4. Alerts: `python desk/brain.py watch` sends breakouts, paper exits, the flat-by warning and watchlist news.

## Replay on real data

`python desk/replay.py --usd 50 --days 20` plays recent real NSE sessions through the whole desk
(scan, plan, risk gate, simulated yes, fills) on a fake account, in its own folder.
It never touches your journal or Telegram. Past results do not predict future ones.

## Data and caveats

Prices are Yahoo Finance 5-minute bars: free, unofficial, can lag. Zerodha charge rates are in
the config; check zerodha.com/charges. Fills are simulated; real fills can be worse.

Design adapted from day-trader-skill (MIT, github.com/Jakeschincariol/day-trader-skill).
