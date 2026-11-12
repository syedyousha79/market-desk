# Market Desk

NSE intraday research and paper trading for a 3-person team. Research and tooling, not advice.

- Read `docs/LESSONS.md` before changing code, and add an entry when something breaks.
- Paper trading only. Never set `paper` to false or place real orders; going live is the user's step.
- In paper mode the approver (`desk/approver.py`, plain code) may say yes/no instead of a person.
  It refuses to run when `paper` is false. Live trading waits for the gates in README (100+ paper trades,
  positive net, Zerodha/SEBI algo setup, broker-side stop orders).
- Telegram is paused (`trader/config.json`); do not unpause or send messages unless the user says so.
- Never read `.env` into the chat or print the token. Tests must stub secrets.
- Plain code owns money rules (risk gate, sizing, exits). AI explains and rates; it never overrides limits.
- Models: Haiku for news rating, Sonnet for explanations and weekly reviews, Opus only for hard research.
- Run `python -m unittest discover -s tests` before every commit.
- The news-to-trade plan is in `docs/news-trading-plan.md`.
