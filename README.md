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
| "Start watching" | /nse-alerts | `desk/alerts.py` |

## Which model does what

Cheap models do the frequent, simple work; bigger ones only where judgement matters.

| Job | Model | Where |
|---|---|---|
| Scanning, sizing, risk checks, paper fills, alerts | none (plain Python) | `desk/` |
| Rating news impact | Haiku | `.claude/agents/news-rater.md` |
| Explaining a trade plan, weekly journal review | Sonnet | planned |
| Deep one-off research | Opus | on request only |

## Your limits (defaults, change in `trader/config.json`)

Account ₹1,000 · max loss ₹25 a trade, ₹25 a day, ₹100 a week · 1 trade a day ·
entries 09:30–14:30 · out by 15:15 · long only · net reward:risk ≥ 1 after Zerodha charges.
Copy `desk/config.example.json` to `trader/config.json` to change anything.

## Setup

1. Python 3.8+ (no packages needed).
2. Telegram: put your bot token in `.env`, send "hello" to the bot, run `python desk/alerts.py setup`.
3. Tests: `python -m unittest discover -s tests -v`
4. Alerts: `python desk/alerts.py watch` sends breakouts, paper exits, the flat-by warning and watchlist news.

## Data and caveats

Prices are Yahoo Finance 5-minute bars: free, unofficial, can lag. Zerodha charge rates are in
the config; check zerodha.com/charges. Fills are simulated; real fills can be worse.

Design adapted from day-trader-skill (MIT, github.com/Jakeschincariol/day-trader-skill).
