---
name: nse-scan
description: NSE market scanner. Opening-range breakouts on the watchlist, pre-open gaps, and Nifty / Bank Nifty / India VIX context. Use when the user says "scan", "any breakouts", "what's moving", "gaps today", "how's the market", or at the start of the morning brief.
---

# nse-scan

Run from the project root:

```bash
python desk/scan.py context          # Nifty 50, Bank Nifty, India VIX
python desk/scan.py gaps             # biggest gaps at today's open
python desk/scan.py orb              # opening-range breakouts so far (writes trader/scan.json)
python desk/scan.py orb --symbols SBIN TMPV
```

- Opening range is 09:15-09:30 IST (5-minute bars from Yahoo, delayed and unofficial).
- A breakout is the first closed bar after the range that closes beyond it on at least
  `volume_multiple` x the usual volume for that time of day.
- Say plainly that a breakout is a setup to look at, not a recommendation.
- Before 09:30 the range is still forming; say so instead of guessing.
- Watchlist and limits live in `trader/config.json` (defaults in `desk/common.py`).
- Next step for a breakout the user likes: `/nse-plan SYMBOL`.
