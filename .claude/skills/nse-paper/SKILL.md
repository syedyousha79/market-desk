---
name: nse-paper
description: Paper trading on NSE. Shows the order ticket, opens a simulated trade only after the user's explicit yes, tracks stop, target and the 15:15 flat-by, and keeps the journal with net P&L after Zerodha charges. Use when the user says "take it", "place it", "buy it", "paper trade", "how are my trades", "close it", "journal", or "how am I doing".
---

# nse-paper

```bash
python desk/paper.py ticket                        # show the order in plain words
python desk/paper.py open --reply "<user's exact words>"
python desk/paper.py update                        # check stop / target / flat-by
python desk/paper.py close SYMBOL                  # exit now at the last price
python desk/paper.py status
python desk/journal.py                             # win rate and net P&L after charges
```

Steps for an entry:
1. `paper.py ticket`. Only a READY ticket goes to the user.
2. Ask: "Place this paper order? Reply yes or no." One order per question. Never offer "yes to all".
3. Run `paper.py open --reply "<their reply, word for word>"`. If it prints "Not recorded",
   ask again. Never turn a reply into a yes yourself.

Never:
- Open a trade from a scheduled or background task: nobody is there to say yes.
- Switch `paper` to false. Live trading is a later, separate step the user turns on themselves,
  through Zerodha's Kite connector with order tools set to "Needs approval".
- Ask for, read, or repeat API keys, tokens or passwords.

This is a simulation from 5-minute bars; real fills can be worse. Not investment advice.
