---
name: nse-alerts
description: Telegram alerts for the NSE desk. Connects the user's bot, sends a test, and runs the watcher that messages new breakouts, paper exits and the flat-by warning. Use when the user says "connect telegram", "send me alerts", "start watching", "test the bot".
---

# nse-alerts

```bash
python desk/alerts.py setup     # after the user sends "hello" to their bot; saves the chat id in .env
python desk/alerts.py test
python desk/alerts.py check     # one pass now
python desk/alerts.py watch     # every 5 minutes; leave it running in a terminal
```

- The token lives only in `.env`, which the user fills in themselves. Never ask for it, print it,
  or read `.env` back into the chat.
- The bot only messages the single chat id in `.env` and ignores everyone else.
- `watch` never places trades. Breakout alerts end with "Ask Claude to plan it".
- If `setup` sees more than one private chat, strangers have messaged the bot: ask the user to
  send "hello" again and run setup straight after.
