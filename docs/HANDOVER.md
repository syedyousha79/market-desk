# Handover: Market Desk (as of 13 Nov 2026)

Read this first, then `CLAUDE.md` and `docs/LESSONS.md` (31 lessons). Everything is paused.

## What this is
NSE research and **paper** trading desk for a 3-person team (user is the Zerodha account holder).
Plain Python (standard library only), run through Claude Code on Windows. Research and tooling, not advice.

## Current state
- Repo: private GitHub `syedyousha79/market-desk`. Local: `D:\Downloads\market-desk`.
- Checked-out branch: `fix/flat-by-label-and-replay-set` = **PR #1, open, not merged** into `main`.
  It holds everything since the flat-by fix (risk tests, rules, weekly/etf/combo backtests, lessons).
- Tests: `python -m unittest discover -s tests` (74 pass). Set `NSE_NO_TELEGRAM=1` when running them.
- Nothing running. Brain only runs when the user starts `python desk/brain.py watch`.
- `trader/config.json` (tracked): `telegram_paused: true`, `auto_approve: true` (paper only).
- `.env` holds the Telegram bot token and chat id (never read or print it). Bot: @marketdeskalertbot.
- Installed globally: ECC plugin (hooks OFF), `uv`. Agent Reach was removed at the user's request.

## Layout
| Path | What |
|---|---|
| `desk/common.py` | IST clock, Yahoo data (`since:YYYY` for daily history), charges, config, message board, Telegram |
| `desk/scan.py`, `signals.py` | ORB scanner; signal agent (fresh, in-play top 3, chase guard, news veto, brief avoid list) |
| `desk/plan.py`, `risk.py` | Sizing (min 1% stop), risk gate (% limits, kill switch 15%, streak, charges ≤10% of risk) |
| `desk/paper.py` | Paper broker + position monitor (break-even +1R, half at +2R, 1R trail, news/time/stale/flat-by exits) |
| `desk/approver.py` | Approver (auto yes/no, paper only) + guardian (emergency halts; `brain.py resume` to lift) |
| `desk/brain.py` | The loop: news -> monitor -> guardian -> signals -> approver; messenger to Telegram |
| `desk/news.py`, `predict.py` | News scout (NSE, ET, BS, Livemint RSS); local predictor (shadow, untrained edge) |
| `desk/replay.py` | Intraday replay of past 5-min sessions (`--name`, `--set KEY=VALUE`, Monday resume) |
| `desk/weekly.py` | Momentum backtest on daily data (`--every month`, `--universe 50`, `--exclude`) |
| `desk/etf.py`, `combo.py` | ETF switching (trend, dual momentum); Fable's 3 combined strategies |
| `.claude/skills/nse-*`, `.claude/agents/` | Skills; agents `news-rater` (Haiku), `morning-brief` (Sonnet) |

## What the tests found (all paper, real prices)
- Intraday ORB: no edge. 60 sessions with new rules -9.6%; the 40 unseen sessions -10.8% (lesson 27).
- Monthly momentum on 50 large caps "beat" Nifty 2022-26 only through survivorship (lesson 29).
- 2015-26 ETFs: hold Nifty +240% (best return); 200-day trend filter +162% with half the drawdown.
- 2013-26 combos: Nifty/gold 70/30 tilt +373% vs Nifty +369% with 22% vs 36% worst drop, carried by
  gold's 2020-26 boom; sector pair not robust; two-key VIX exit lagged (lesson 31).
- Buy-and-hold since 2013 (Rs 10k): Nasdaq 100 ETF ~Rs 2.06L, Next 50 ~Rs 64k, Nifty ~Rs 47k, gold ~Rs 40k.
- Conclusion shared with the user: nothing built beat simply holding the index at Rs 10k-1L.

## User preferences (important)
- Keep usage low: set sub-agent models explicitly (Sonnet research, Haiku simple); no Opus sub-agents.
  The user asked **not to use Fable for thinking** any more.
- Ask before resuming any halt; never assume the laptop is on; nothing auto-starts.
- Paper only; live trading postponed (gates in README). Telegram stays paused unless told.
- Short, plain answers with numbers; honest about losses. No personalised stock picks
  (not a licensed adviser); give criteria and data.
- Commit and push after each change (to the PR branch); no attribution lines in commits.

## Open items (only if the user asks)
1. Merge PR #1 into `main`.
2. Optional tests offered: "buy in steps after 10/20/30% falls" on Nifty; actual Nvidia vs Intel vs Nasdaq 100.
3. First live paper run in market hours (user starts it).
4. Predictor stays shadow; people tracker paused (needs accounts with plain stock calls; StoxBox scored
   7 Nov calls, -0.31R avg).
